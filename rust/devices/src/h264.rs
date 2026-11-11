//! What devices/h264.py says of H.264 streams and of ffmpeg beyond the engine: the colour tags
//! every encoder is told, the stamp put into every stream's SPS, the slices of a stream, and
//! where ffmpeg is (find_ffmpeg).

use std::path::{Path, PathBuf};

/// The kinds of NAL unit that carry a picture's slice.
const NAL_SLICE: u8 = 1;
const NAL_IDR: u8 = 5;

/// Full-range YUV for a panel whose decoder shows every stream as full range: swscale converts
/// the turned frame with BT.601 into 0-255 (FULL_RANGE).
pub const FULL_RANGE: &str = "scale=out_range=full:out_color_matrix=bt601";

/// colour_tags: BT.601 said in full, in limited range or (`full`) full range.
pub fn colour_tags(full: bool) -> Vec<String> {
    let range = if full { "pc" } else { "tv" };
    [
        "-colorspace",
        "smpte170m",
        "-color_primaries",
        "smpte170m",
        "-color_trc",
        "smpte170m",
        "-color_range",
        range,
    ]
    .map(String::from)
    .to_vec()
}

/// stamp: the same stamped into every stream's SPS (its VUI) after the encoder.
pub fn stamp(full: bool) -> Vec<String> {
    vec![
        "-bsf:v".into(),
        format!(
            "h264_metadata=video_full_range_flag={}:colour_primaries=6\
             :transfer_characteristics=6:matrix_coefficients=6",
            u8::from(full)
        ),
    ]
}

/// nal_units: (offset, type) of every NAL unit in an Annex B stream.
pub fn nal_units(data: &[u8]) -> Vec<(usize, u8)> {
    let mut found = Vec::new();
    let mut pos = 0;
    while let Some(i) = data[pos..]
        .windows(3)
        .position(|w| w == [0, 0, 1])
        .map(|i| i + pos)
    {
        if i + 3 >= data.len() {
            break;
        }
        found.push((i + 3, data[i + 3] & 0x1F));
        pos = i + 3;
    }
    found
}

/// slice_count: the slices of a stream (a picture's or a keyframe's).
pub fn slice_count(data: &[u8]) -> usize {
    nal_units(data)
        .iter()
        .filter(|(_, kind)| matches!(*kind, NAL_SLICE | NAL_IDR))
        .count()
}

fn program_name(name: &str) -> String {
    if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    }
}

/// find_ffmpeg: the ffmpeg program: the configured path, the one a download carries (its
/// `tools` folder), PATH, or next to Libre Panel. With the highest rights only one from a
/// safe place (rights.rs): a configured one elsewhere is passed over for the others. The
/// error says why there is none, as Python's DeviceError says it.
pub fn find_ffmpeg(configured: &str) -> Result<PathBuf, String> {
    let exe = program_name("ffmpeg");
    let mut held_back = false;
    if !configured.is_empty() {
        let path = expand_user(configured);
        if !path.is_file() {
            return Err(
                lp_i18n::t("ffmpeg not found at {path} (video.ffmpeg in config.toml)")
                    .replace("{path}", &path.display().to_string()),
            );
        }
        if crate::rights::safe_place(&path) {
            return Ok(path);
        }
        held_back = true;
    }
    let beside = std::env::current_exe()
        .ok()
        .map(|program| std::fs::canonicalize(&program).unwrap_or(program))
        .and_then(|program| program.parent().map(Path::to_path_buf));
    if let Some(own) = beside
        .as_ref()
        .map(|folder| folder.join("tools").join(&exe))
    {
        if own.is_file() {
            return Ok(own); // (first: the build the download was tested with)
        }
    }
    if let Some(found) = crate::rights::which("ffmpeg", &mut Vec::new()) {
        return Ok(found);
    }
    if let Some(next) = beside.map(|folder| folder.join(&exe)) {
        if next.is_file() && crate::rights::safe_place(&next) {
            return Ok(next);
        }
    }
    Err(if held_back {
        lp_i18n::t(
            "with administrator rights Libre Panel does not start the ffmpeg in video.ffmpeg: \
             it lies in a folder that other programs may change",
        )
    } else {
        lp_i18n::t(
            "the video mode needs ffmpeg: the downloads of Libre Panel carry it; \
             with pip install it, or set video.ffmpeg in config.toml to the ffmpeg program",
        )
    })
}

/// Path.expanduser: a leading ~ as the person's own folder.
fn expand_user(path: &str) -> PathBuf {
    let home = || {
        let named = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
        std::env::var_os(named).map(PathBuf::from)
    };
    match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with(['/', '\\']) => match home() {
            Some(home) => home.join(rest.trim_start_matches(['/', '\\'])),
            None => PathBuf::from(path),
        },
        _ => PathBuf::from(path),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_slices_of_a_stream_are_counted_as_h264_py_counts_them() {
        // SPS, PPS, IDR, then a slice; a start code at the very end is no unit
        let stream = [
            0, 0, 0, 1, 0x67, 9, 0, 0, 1, 0x68, 9, 0, 0, 1, 0x65, 9, 9, 0, 0, 1, 0x41, 0, 0, 1,
        ];
        let kinds: Vec<u8> = nal_units(&stream).iter().map(|(_, k)| *k).collect();
        assert_eq!(kinds, [7, 8, 5, 1]);
        assert_eq!(slice_count(&stream), 2);
    }
}
