// What a Windows program of Libre Panel's says of itself (shared by the crates' build.rs):
// its version resource (who made it, what it is, which version, as the Python app's
// packaging/pyinstaller/libre-panel.spec version_resource and the setup say: what Explorer and
// Windows Security show, and what an antivirus program weighs), its icon, and its manifest
// (the common controls of version 6, for dialogs with buttons of their own words; per-monitor
// DPI; Windows 10 and 11; no elevation asked). Nothing elsewhere.

/// The release's version (src/libre_panel/__init__.py's __version__) and its four numbers
/// as the release's are made: a hotfix's (0.6.8.1) as it is, 0.8.1rc2 with a 0.
fn release_version(root: &std::path::Path) -> (String, [u32; 4]) {
    let source = root.join("src/libre_panel/__init__.py");
    println!("cargo:rerun-if-changed={}", source.display());
    let text = std::fs::read_to_string(&source).unwrap_or_default();
    let version = text
        .lines()
        .find_map(|line| {
            let value = line.strip_prefix("__version__")?.trim_start().strip_prefix('=')?;
            let value = value.trim();
            let quote = value.chars().next().filter(|c| *c == '"' || *c == '\'')?;
            Some(value[1..].split(quote).next()?.to_string())
        })
        .unwrap_or_else(|| "0.0.0".to_string());
    let plain = version.chars().all(|c| c.is_ascii_digit() || c == '.');
    let mut numbers = [0u32; 4];
    let found: Vec<u32> = version
        .split(|c: char| !c.is_ascii_digit())
        .filter(|part| !part.is_empty())
        .filter_map(|part| part.parse().ok())
        .take(if plain { 4 } else { 3 })
        .collect();
    numbers[..found.len()].copy_from_slice(&found);
    (version, numbers)
}

/// The resources of the programs `bins` ((name, what it is)), compiled into each of them.
fn windows_resources(bins: &[(&str, &str)]) {
    let target = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target != "windows" {
        return;
    }
    let crate_dir = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let root = crate_dir.join("../..");
    let out = std::path::PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let (version, [a, b, c, d]) = release_version(&root);
    let icon = root.join("packaging/icons/libre-panel.ico");
    println!("cargo:rerun-if-changed={}", icon.display());
    let quoted = |path: &std::path::Path| path.display().to_string().replace('\\', "/");
    let manifest = out.join("libre-panel.manifest");
    let text = r#"<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*"/>
    </dependentAssembly>
  </dependency>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"/>
    </application>
  </compatibility>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/>
      </requestedPrivileges>
    </security>
  </trustInfo>
</assembly>
"#;
    std::fs::write(&manifest, text).expect("the manifest");
    for (name, description) in bins {
        let strings = [
            ("CompanyName", "Libre Panel contributors"),
            ("FileDescription", description),
            ("FileVersion", version.as_str()),
            ("InternalName", name),
            ("LegalCopyright", "Libre Panel contributors, GPL-3.0-or-later"),
            ("OriginalFilename", &format!("{name}.exe")),
            ("ProductName", "Libre Panel"),
            ("ProductVersion", version.as_str()),
        ];
        let values: String = strings
            .iter()
            .map(|(key, value)| format!("      VALUE \"{key}\", \"{value}\\0\"\n"))
            .collect();
        let rc = format!(
            "1 ICON \"{icon}\"\n\
             1 24 \"{manifest}\"\n\
             1 VERSIONINFO\n\
             FILEVERSION {a},{b},{c},{d}\n\
             PRODUCTVERSION {a},{b},{c},{d}\n\
             FILEFLAGSMASK 0x3F\n\
             FILEFLAGS 0x0\n\
             FILEOS 0x40004\n\
             FILETYPE 0x1\n\
             FILESUBTYPE 0x0\n\
             BEGIN\n  BLOCK \"StringFileInfo\"\n  BEGIN\n    BLOCK \"040904B0\"\n    BEGIN\n\
             {values}    END\n  END\n  BLOCK \"VarFileInfo\"\n  BEGIN\n\
             \x20   VALUE \"Translation\", 0x409, 1200\n  END\nEND\n",
            icon = quoted(&icon),
            manifest = quoted(&manifest),
        );
        let file = out.join(format!("{name}.rc"));
        std::fs::write(&file, rc).expect("the resource script");
        embed_resource::compile_for(&file, [name], embed_resource::NONE)
            .manifest_required()
            .expect("the program's resources");
    }
}
