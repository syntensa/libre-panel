//! A stream's encoder: x264 on the processor, or where asked for (`--encoder card`) the
//! graphics card's through Windows' Media Foundation (mf.rs), with x264 again wherever the
//! card's fails: to open (no such card here, or it does not take the panels' stream) or in
//! the running stream (it goes on with x264 from a keyframe). `--encoder auto` asks for the
//! card's where it is AMD's or NVIDIA's ([`proven`]), else x264. The log says which it
//! is, and why x264 where the card was asked for.

use crate::x264;

/// Which encoder a stream asks for (`--encoder x264|card|auto`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Wanted {
    /// x264 on the processor
    X264,
    /// the graphics card's where it opens, else x264
    Card,
    /// the graphics card's where it is proven with the panels ([`proven`]) and opens, else x264
    Proven,
}

impl Wanted {
    pub fn parse(text: &str) -> Option<Wanted> {
        match text {
            "x264" => Some(Wanted::X264),
            "card" => Some(Wanted::Card),
            "auto" => Some(Wanted::Proven),
            _ => None,
        }
    }
}

/// Whether the graphics card's encoder Windows names so is the default's: AMD's
/// ("AMDh264Encoder"), proven with the panels (its pictures as x264's to the eye on a 12.3" and
/// a 9.2" at 25 frames a second, the engines' memory less: two panels 68 MB instead of 90), and
/// NVIDIA's ("NVIDIA H.264 Encoder MFT"), of the same kind; Intel's is not yet: x264 encodes for
/// it unless the card's is asked for. Where one does not start or stops, x264 encodes.
#[cfg_attr(not(windows), allow(dead_code))] // (Media Foundation's names: Windows)
pub fn proven(name: &str) -> bool {
    let name = name.to_ascii_lowercase();
    name.contains("amd") || name.contains("nvidia")
}

pub struct Encoder {
    x264: Option<x264::Encoder>,
    #[cfg(windows)]
    card: Option<crate::mf::Encoder>,
    /// x264's settings, with the bit rate as it is now: for x264 after the card
    settings: x264::Settings,
    /// the card's last picture, as x264 keeps its own
    #[cfg(windows)]
    picture: Vec<u8>,
    /// whether only a card's encoder that is proven may encode (`--encoder auto`)
    #[cfg(windows)]
    proven_only: bool,
}

/// A line for Libre Panel's log, said as it is (devices/engine.py logs it as a note).
pub fn note(text: &str) {
    eprintln!("libre-panel-engine (note): {text}");
}

impl Encoder {
    /// The encoder of a stream with `settings`: the card's where `wanted` asks for it (and, for
    /// [`Wanted::Proven`], it is proven) and it opens, else x264.
    pub fn open(settings: &x264::Settings, wanted: Wanted) -> Result<Encoder, String> {
        let mut encoder = Encoder {
            x264: None,
            #[cfg(windows)]
            card: None,
            settings: settings.clone(),
            #[cfg(windows)]
            picture: Vec::new(),
            #[cfg(windows)]
            proven_only: wanted == Wanted::Proven,
        };
        match wanted {
            Wanted::X264 => {}
            Wanted::Card => encoder.open_card(),
            // (elsewhere than on Windows x264, as it would be: no note)
            Wanted::Proven if cfg!(windows) => encoder.open_card(),
            Wanted::Proven => {}
        }
        if !encoder.on_card() {
            encoder.x264 = Some(x264::Encoder::open(settings)?);
        }
        Ok(encoder)
    }

    /// The card's encoder with the stream's settings as they are now.
    #[cfg(windows)]
    fn card_now(&self) -> Result<crate::mf::Encoder, String> {
        let s = &self.settings;
        crate::mf::Encoder::open(&crate::mf::Settings {
            width: s.width,
            height: s.height,
            fps: s.fps,
            kbit: s.kbit,
            keyint: s.keyint,
            profile: s.profile.clone(),
            full: s.full,
            proven_only: self.proven_only,
        })
    }

    #[cfg(windows)]
    fn open_card(&mut self) {
        match self.card_now() {
            Ok(card) => {
                note(&format!(
                    "the stream's encoder: {} (the graphics card's)",
                    card.name
                ));
                self.card = Some(card);
            }
            Err(e) => note(&format!("{e}; x264 instead")),
        }
    }

    #[cfg(not(windows))]
    fn open_card(&mut self) {
        note("the card's encoder is Windows' (Media Foundation); x264 here");
    }

    /// Whether the card encodes (else x264).
    pub fn on_card(&self) -> bool {
        #[cfg(windows)]
        {
            self.card.is_some()
        }
        #[cfg(not(windows))]
        {
            false
        }
    }

    /// x264 from here on, where the card's failed: `why` to the log. Its first picture is a
    /// keyframe, with its SPS and PPS.
    #[cfg(windows)]
    fn fall_back(&mut self, why: &str) -> Result<(), String> {
        note(&format!("{why}; x264 from here on"));
        self.card = None;
        self.x264 = Some(x264::Encoder::open(&self.settings)?);
        Ok(())
    }

    /// One picture of the planes (I420), told which macroblocks stayed the same (x264's): its
    /// NAL units (Annex B), empty where the encoder has none for it yet.
    pub fn encode(
        &mut self,
        y: &mut [u8],
        u: &mut [u8],
        v: &mut [u8],
        stride: usize,
        mb: &mut [u8],
        pts: i64,
    ) -> Result<&[u8], String> {
        #[cfg(windows)]
        if let Some(card) = self.card.as_mut() {
            match card.encode(y, u, v, stride, pts) {
                Ok(picture) => {
                    self.picture = picture;
                    return Ok(&self.picture);
                }
                Err(e) => self.fall_back(&e)?,
            }
        }
        let x264 = self.x264.as_mut().ok_or("no encoder")?;
        let (picture, _key) = x264.encode(y, u, v, stride, mb, pts)?;
        Ok(picture)
    }

    /// The bit rate's cap, changed in the running stream.
    pub fn rate(&mut self, kbit: u32) -> Result<(), String> {
        self.settings.kbit = kbit;
        #[cfg(windows)]
        if let Some(card) = self.card.as_mut() {
            let Err(refused) = card.rate(kbit) else {
                return Ok(());
            };
            // one that takes no new rate while it runs is opened anew with it (a keyframe),
            // else x264 takes it: the rate "auto" follows the panel
            self.card = None; // (its session closed first: a card's are few)
            return match self.card_now() {
                Ok(card) => {
                    note(&format!(
                        "{refused}: the card's encoder opened anew at {kbit} kbit/s"
                    ));
                    self.card = Some(card);
                    Ok(())
                }
                Err(e) => self.fall_back(&format!("{refused}; {e}")),
            };
        }
        match self.x264.as_mut() {
            Some(x264) => x264.rate(kbit),
            None => Ok(()),
        }
    }

    /// The next picture a keyframe.
    pub fn keyframe(&mut self) {
        #[cfg(windows)]
        if let Some(card) = self.card.as_mut() {
            card.keyframe();
            return;
        }
        if let Some(x264) = self.x264.as_mut() {
            x264.keyframe();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_cards_encoder_asked_for_where_it_does_not_open_is_x264() {
        let settings = x264::Settings {
            width: 64,
            height: 32,
            fps: 25,
            crf: 23.0,
            kbit: 1000,
            keyint: 25,
            preset: "ultrafast".into(),
            profile: "high".into(),
            full: false,
            extra: String::new(),
        };
        let mut plain = Encoder::open(&settings, Wanted::X264).unwrap();
        assert!(!plain.on_card());
        let mut asked = Encoder::open(&settings, Wanted::Card).unwrap();
        // (a Windows with a graphics card's encoder takes it: the stream is the card's then)
        if cfg!(windows) && asked.on_card() {
            return;
        }
        assert!(!asked.on_card());
        // (auto: x264 where no card's encoder opens, and elsewhere than on Windows)
        let mut auto = Encoder::open(&settings, Wanted::Proven).unwrap();
        assert!(!auto.on_card());
        let (mut y, mut u, mut v) = (vec![90u8; 64 * 32], vec![128u8; 512], vec![128u8; 512]);
        let mut mb = vec![0u8; 8];
        for pts in 0..3 {
            let a = plain
                .encode(&mut y, &mut u, &mut v, 64, &mut mb, pts)
                .unwrap()
                .to_vec();
            let b = asked
                .encode(&mut y, &mut u, &mut v, 64, &mut mb, pts)
                .unwrap()
                .to_vec();
            let c = auto
                .encode(&mut y, &mut u, &mut v, 64, &mut mb, pts)
                .unwrap()
                .to_vec();
            assert!(!a.is_empty());
            assert_eq!(a, b, "x264 alike, frame {pts}");
            assert_eq!(a, c, "x264 alike (auto), frame {pts}");
        }
        asked.rate(500).unwrap();
        asked.keyframe();
        assert!(!asked
            .encode(&mut y, &mut u, &mut v, 64, &mut mb, 3)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn auto_takes_only_a_card_s_encoder_proven_with_the_panels() {
        assert!(proven("AMDh264Encoder"));
        assert!(proven("AMD H.264 Hardware MFT Encoder"));
        assert!(proven("NVIDIA H.264 Encoder MFT"));
        assert!(!proven("Intel(R) Quick Sync Video H.264 Encoder MFT"));
        assert!(!proven("a graphics card's H.264 encoder")); // (no name to be had)
        assert_eq!(Wanted::parse("auto"), Some(Wanted::Proven));
        assert_eq!(Wanted::parse("card"), Some(Wanted::Card));
        assert_eq!(Wanted::parse("x264"), Some(Wanted::X264));
        assert_eq!(Wanted::parse("gpu"), None);
    }
}
