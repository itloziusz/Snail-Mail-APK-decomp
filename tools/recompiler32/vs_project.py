"""Export an editable Visual Studio 2022 x64 console project for A32 C output."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}\Z")


def project_files(data: bytes, c_source: str, base: int, max_steps: int,
                  name: str = "A32Recompiled") -> dict[str, bytes]:
    if not NAME_RE.fullmatch(name):
        raise ValueError("project name must be 1..40 ASCII letters, digits, or underscores, starting with a letter")
    guid = "{" + str(uuid.uuid4()).upper() + "}"
    project = f"{name}.vcxproj"
    source = f"{name}.c"
    configurations = [("Debug", "Disabled", "MultiThreadedDebugDLL"),
                      ("Release", "MaxSpeed", "MultiThreadedDLL")]
    xml = ['<?xml version="1.0" encoding="utf-8"?>',
           '<Project DefaultTargets="Build" ToolsVersion="Current" '
           'xmlns="http://schemas.microsoft.com/developer/msbuild/2003">',
           '  <ItemGroup Label="ProjectConfigurations">']
    for config, _, _ in configurations:
        xml += [f'    <ProjectConfiguration Include="{config}|x64">',
                f'      <Configuration>{config}</Configuration>',
                '      <Platform>x64</Platform>',
                '    </ProjectConfiguration>']
    xml += ['  </ItemGroup>',
            '  <PropertyGroup Label="Globals">',
            '    <VCProjectVersion>17.0</VCProjectVersion>',
            '    <Keyword>Win32Proj</Keyword>',
            f'    <ProjectGuid>{guid}</ProjectGuid>',
            f'    <RootNamespace>{name}</RootNamespace>',
            '    <WindowsTargetPlatformVersion>10.0</WindowsTargetPlatformVersion>',
            '  </PropertyGroup>',
            '  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.Default.props" />']
    for config, _, _ in configurations:
        xml += [f'  <PropertyGroup Condition="\'$(Configuration)|$(Platform)\'==\'{config}|x64\'" Label="Configuration">',
                '    <ConfigurationType>Application</ConfigurationType>',
                '    <PlatformToolset>v143</PlatformToolset>',
                '    <CharacterSet>Unicode</CharacterSet>',
                '  </PropertyGroup>']
    xml += ['  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.props" />',
            '  <ImportGroup Label="ExtensionSettings" />',
            '  <PropertyGroup Label="UserMacros" />',
            '  <PropertyGroup>',
            '    <OutDir>$(ProjectDir)build\\$(Platform)\\$(Configuration)\\</OutDir>',
            '    <IntDir>$(ProjectDir)build\\obj\\$(Platform)\\$(Configuration)\\</IntDir>',
            '  </PropertyGroup>']
    for config, optimization, runtime in configurations:
        xml += [f'  <ItemDefinitionGroup Condition="\'$(Configuration)|$(Platform)\'==\'{config}|x64\'">',
                '    <ClCompile>',
                '      <WarningLevel>Level4</WarningLevel>',
                '      <SDLCheck>true</SDLCheck>',
                '      <PrecompiledHeader>NotUsing</PrecompiledHeader>',
                '      <CompileAs>CompileAsC</CompileAs>',
                '      <AdditionalOptions>/std:c11 %(AdditionalOptions)</AdditionalOptions>',
                f'      <Optimization>{optimization}</Optimization>',
                f'      <RuntimeLibrary>{runtime}</RuntimeLibrary>',
                '    </ClCompile>',
                '    <Link><SubSystem>Console</SubSystem><GenerateDebugInformation>true</GenerateDebugInformation></Link>',
                '  </ItemDefinitionGroup>']
    xml += [f'  <ItemGroup><ClCompile Include="{source}" /></ItemGroup>',
            '  <ItemGroup>',
            '    <None Include="source.a32" />',
            '    <None Include="manifest.json" />',
            '    <None Include="README.md" />',
            '  </ItemGroup>',
            '  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />',
            '</Project>']
    sln = ['Microsoft Visual Studio Solution File, Format Version 12.00',
           '# Visual Studio Version 17',
           'VisualStudioVersion = 17.0.31903.59',
           'MinimumVisualStudioVersion = 10.0.40219.1',
           f'Project("{{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}}") = "{name}", "{project}", "{guid}"',
           'EndProject', 'Global',
           '  GlobalSection(SolutionConfigurationPlatforms) = preSolution',
           '    Debug|x64 = Debug|x64', '    Release|x64 = Release|x64',
           '  EndGlobalSection',
           '  GlobalSection(ProjectConfigurationPlatforms) = postSolution']
    for config, _, _ in configurations:
        sln += [f'    {guid}.{config}|x64.ActiveCfg = {config}|x64',
                f'    {guid}.{config}|x64.Build.0 = {config}|x64']
    sln += ['  EndGlobalSection', 'EndGlobal']
    manifest = {"schema": 1, "project": name, "source_sha256": hashlib.sha256(data).hexdigest(),
                "base": f"0x{base:08x}", "max_steps": max_steps,
                "source_file": source, "raw_file": "source.a32"}
    readme = f"""# {name} — editable A32 recompilation starter

Open `{name}.sln` in Visual Studio 2022 with the **Desktop development with C++**
workload and a Windows SDK installed. Choose **Debug | x64** or **Release | x64**
and build. This project uses MSVC's C11 mode and produces a console `.exe`.

Edit `{source}` directly in Solution Explorer. The `run` function contains
one case per guest instruction; you may replace or refactor these cases and
add your own C code. Build in Visual Studio does **not** run the Python
recompiler or overwrite edits. The original input is `source.a32`; `manifest.json`
records its SHA-256, base address, and translation step limit.

To regenerate, export to a **new directory**, then compare and carry over
your changes by hand. The exporter refuses to replace an existing directory.
Do not treat this tiny A32 instruction subset as a general game port. Platform
APIs, memory, graphics, audio, and game-specific calls still need implementation.
Only share the original input bytes if you have the rights to do so.
"""
    return {f"{name}.sln": ("\ufeff" + "\r\n".join(sln) + "\r\n").encode("utf-8"),
            project: ("\r\n".join(xml) + "\r\n").encode("utf-8"),
            source: c_source.encode("utf-8"),
            "source.a32": data,
            "manifest.json": (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            "README.md": readme.encode("utf-8"),
            ".gitignore": b".vs/\nbuild/\n*.user\n"}


def export_project(data: bytes, c_source: str, base: int, max_steps: int,
                   destination: Path, name: str = "A32Recompiled") -> None:
    files = project_files(data, c_source, base, max_steps, name)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {destination.parent}")
    with tempfile.TemporaryDirectory(prefix=".a32-project-", dir=destination.parent) as temp:
        os.chmod(temp, 0o700)
        for filename, content in files.items():
            (Path(temp) / filename).write_bytes(content)
        os.rename(temp, destination)
