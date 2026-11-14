//! LibrePanel.exe: Libre Panel without a console window (Windows): the program the start
//! menu, the start at login and the setup start. The tray app unless a command is given, as
//! the Python downloads' LibrePanel.exe (lib.rs is all of it).

#![cfg_attr(windows, windows_subsystem = "windows")]

lp_app::program!("tray");
