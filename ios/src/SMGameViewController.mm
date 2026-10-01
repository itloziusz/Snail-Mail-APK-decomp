#import "SMGameViewController.h"
#import "SMIOSHost.h"

#import <CoreMotion/CoreMotion.h>
#import <OpenGLES/ES2/gl.h>
#import <OpenGLES/ES2/glext.h>
#import <QuartzCore/CAEAGLLayer.h>
#import <QuartzCore/CADisplayLink.h>

#include <math.h>
#include "sm_rendering/smgl.h"

@interface SMGameView : UIView
@end
@implementation SMGameView
+ (Class)layerClass { return CAEAGLLayer.class; }
@end

@interface SMGameViewController : UIViewController
@end

@implementation SMGameViewController {
    EAGLContext *_context;
    GLuint _framebuffer, _colorbuffer, _depthbuffer;
    GLint _pixelWidth, _pixelHeight;
    CADisplayLink *_displayLink;
    CMMotionManager *_motion;
    BOOL _booted;
    BOOL _active;
    NSTimeInterval _lastMotionTime;
    float _lastX, _lastY, _lastZ;
    BOOL _haveMotion;
}

- (void)loadView {
    SMGameView *v = [[SMGameView alloc] initWithFrame:UIScreen.mainScreen.bounds];
    v.multipleTouchEnabled = NO;
    v.contentScaleFactor = UIScreen.mainScreen.scale;
    CAEAGLLayer *layer = (CAEAGLLayer *)v.layer;
    layer.opaque = YES;
    layer.contentsScale = v.contentScaleFactor;
    layer.drawableProperties = @{ kEAGLDrawablePropertyRetainedBacking : @NO,
                                  kEAGLDrawablePropertyColorFormat : kEAGLColorFormatRGBA8 };
    self.view = v;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.blackColor;
    _active = YES;
    _context = [[EAGLContext alloc] initWithAPI:kEAGLRenderingAPIOpenGLES2];
    NSAssert(_context != nil, @"OpenGL ES 2 context creation failed");
    [EAGLContext setCurrentContext:_context];

    _motion = [CMMotionManager new];
    if (_motion.accelerometerAvailable) {
        _motion.accelerometerUpdateInterval = 1.0 / 120.0;
        [_motion startAccelerometerUpdates];
    }

    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(willResign:)
                                                 name:UIApplicationWillResignActiveNotification object:nil];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(didBecome:)
                                                 name:UIApplicationDidBecomeActiveNotification object:nil];
}

- (BOOL)prefersStatusBarHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
- (BOOL)shouldAutorotate { return YES; }

- (void)dealloc {
    [_displayLink invalidate];
    [_motion stopAccelerometerUpdates];
    [[NSNotificationCenter defaultCenter] removeObserver:self];
    [EAGLContext setCurrentContext:_context];
    if (_depthbuffer) glDeleteRenderbuffers(1, &_depthbuffer);
    if (_colorbuffer) glDeleteRenderbuffers(1, &_colorbuffer);
    if (_framebuffer) glDeleteFramebuffers(1, &_framebuffer);
    smgl_shutdown();
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    [self rebuildDrawable];
}

- (void)rebuildDrawable {
    [EAGLContext setCurrentContext:_context];
    if (_depthbuffer) { glDeleteRenderbuffers(1, &_depthbuffer); _depthbuffer = 0; }
    if (_colorbuffer) { glDeleteRenderbuffers(1, &_colorbuffer); _colorbuffer = 0; }
    if (_framebuffer) { glDeleteFramebuffers(1, &_framebuffer); _framebuffer = 0; }

    glGenFramebuffers(1, &_framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, _framebuffer);
    glGenRenderbuffers(1, &_colorbuffer);
    glBindRenderbuffer(GL_RENDERBUFFER, _colorbuffer);
    [_context renderbufferStorage:GL_RENDERBUFFER fromDrawable:(CAEAGLLayer *)self.view.layer];
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_RENDERBUFFER, _colorbuffer);
    glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_WIDTH, &_pixelWidth);
    glGetRenderbufferParameteriv(GL_RENDERBUFFER, GL_RENDERBUFFER_HEIGHT, &_pixelHeight);

    glGenRenderbuffers(1, &_depthbuffer);
    glBindRenderbuffer(GL_RENDERBUFFER, _depthbuffer);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT16, _pixelWidth, _pixelHeight);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, _depthbuffer);
    NSAssert(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE, @"iOS framebuffer incomplete");

    if (!_booted && _pixelWidth > 0 && _pixelHeight > 0) {
        NSAssert(smgl_init() == 0, @"GLES1-on-GLES2 bootstrap failed");
        NSString *resource = NSBundle.mainBundle.resourcePath;
        NSString *assets = [resource stringByAppendingPathComponent:@"assets"];
        if (![[NSFileManager defaultManager] fileExistsAtPath:[assets stringByAppendingPathComponent:@"asm.mp3"]]) {
            assets = [[resource stringByAppendingPathComponent:@"Resources"] stringByAppendingPathComponent:@"assets"];
        }
        NSArray<NSURL *> *urls = [[NSFileManager defaultManager] URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask];
        NSURL *filesURL = urls.firstObject ?: [NSURL fileURLWithPath:NSTemporaryDirectory() isDirectory:YES];
        NSString *files = [filesURL.path stringByAppendingPathComponent:@"SnailMail"];
        int rc = sm_ios_host_boot(assets.fileSystemRepresentation, files.fileSystemRepresentation, _pixelWidth, _pixelHeight);
        NSAssert(rc == 0, @"Snail Mail iOS bootstrap failed: %s", sm_ios_host_last_error() ?: "unknown");
        _booted = (rc == 0);
        [self startDisplayLink];
    } else if (_booted) {
        sm_ios_host_resize(_pixelWidth, _pixelHeight);
    }
}

- (void)startDisplayLink {
    if (_displayLink) return;
    _displayLink = [CADisplayLink displayLinkWithTarget:self selector:@selector(drawFrame:)];
    if (@available(iOS 15.0, *)) {
        _displayLink.preferredFrameRateRange = CAFrameRateRangeMake(60.0, 60.0, 60.0);
    } else {
        _displayLink.preferredFramesPerSecond = 60;
    }
    [_displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
}

- (void)drawFrame:(CADisplayLink *)link {
    (void)link;
    if (!_booted || !_active) return;
    [EAGLContext setCurrentContext:_context];
    glBindFramebuffer(GL_FRAMEBUFFER, _framebuffer);
    [self sampleMotion];
    sm_ios_host_render(0);
    glBindRenderbuffer(GL_RENDERBUFFER, _colorbuffer);
    [_context presentRenderbuffer:GL_RENDERBUFFER];
}

- (void)sampleMotion {
    CMAccelerometerData *d = _motion.accelerometerData;
    if (!d) return;
    double ax = -d.acceleration.x, ay = -d.acceleration.y, az = -d.acceleration.z;
    double n = sqrt(ax * ax + ay * ay + az * az);
    if (!(n > 0.0) || !isfinite(n)) return;
    float x = (float)(ax / n), y = (float)(ay / n), z = (float)(az / n);
    NSTimeInterval now = d.timestamp;
    NSTimeInterval elapsed = now - _lastMotionTime;
    if (!_haveMotion || elapsed <= 0.0 || elapsed > 0.5) {
        _lastX = x; _lastY = y; _lastZ = z; _haveMotion = YES;
    } else {
        float dt = fminf((float)elapsed, 0.05f);
        float dx = x - _lastX, dy = y - _lastY, dz = z - _lastZ;
        float tau = dx * dx + dy * dy + dz * dz > 0.18f * 0.18f ? 0.028f : 0.070f;
        float alpha = -expm1f(-dt / tau);
        _lastX += dx * alpha; _lastY += dy * alpha; _lastZ += dz * alpha;
    }
    _lastMotionTime = now;
    /* iOS raw accelerometer gravity sign is inverted into Android device-space above.
       Now apply the Android shell's final (-x,-y,z) mapping. */
    sm_ios_host_accelerometer(-_lastX, -_lastY, _lastZ);
}

- (void)sendTouch:(UITouch *)touch action:(int)action {
    CGPoint p = [touch locationInView:self.view];
    CGFloat scale = self.view.contentScaleFactor;
    sm_ios_host_touch(action, (float)(p.x * scale), (float)(p.y * scale));
}
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { (void)event; [self sendTouch:touches.anyObject action:0]; }
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { (void)event; [self sendTouch:touches.anyObject action:1]; }
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { (void)event; [self sendTouch:touches.anyObject action:2]; }
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event { (void)event; [self sendTouch:touches.anyObject action:2]; }

- (void)willResign:(NSNotification *)n {
    (void)n;
    _active = NO;
    _displayLink.paused = YES;
    [_motion stopAccelerometerUpdates];
}

- (void)didBecome:(NSNotification *)n {
    (void)n;
    _active = YES;
    _displayLink.paused = NO;
    if (_motion.accelerometerAvailable && !_motion.accelerometerActive) [_motion startAccelerometerUpdates];
    _haveMotion = NO;
}
@end

UIViewController *SMCreateGameViewController(void) {
    return [SMGameViewController new];
}
