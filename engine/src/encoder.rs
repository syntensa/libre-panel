//! A stream's encoder: x264 on the processor, or where asked for (`--encoder card`) the
//! graphics card's through Windows' Media Foundation (mf.rs), with x264 again wherever the
//! card's fails: to open (no such card here, or it does not take the panels' stream) or in
//! the running stream (it goes on with x264 from a keyframe). The log says which it is, and
//! why x264 where the card was asked for.

use crate::x264;

pub struct Encoder {
    x264: Option<x264::Encoder>,
    #[cfg(windows)]
    card: Option<crate::mf::Encoder>,
    /// x264's settings, with the bit rate as it is now: for x264 after the card
    settings: x264::Settings,
    /// the card's last picture, as x264 keeps its own
    #[cfg(windows)]
    picture: Vec<u8>,
}

/// A line for Libre Panel's log, said as it is (devices/engine.py logs it as a note).
pub fn note(text: &str) {
    eprintln!("libre-panel-engine (note): {text}");
}

impl Encoder {
    /// The encoder of a stream with `settings`: the card's where `card` asks for it and it
    /// opens, else x264.
    pub fn open(settings: &x264::Settings, card: bool) -> Result<Encoder, String> {
        let mut encoder = Encoder {
            x264: None,
            #[cfg(windows)]
            card: None,
            settings: settings.clone(),
            #[cfg(windows)]
            picture: Vec::new(),
        };
        if card {
            encoder.open_card();
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
        let mut plain = Encoder::open(&settings, false).unwrap();
        assert!(!plain.on_card());
        let mut asked = Encoder::open(&settings, true).unwrap();
        // (a Windows with a graphics card's encoder takes it: the stream is the card's then)
        if cfg!(windows) && asked.on_card() {
            return;
        }
        assert!(!asked.on_card());
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
            assert!(!a.is_empty());
            assert_eq!(a, b, "x264 alike, frame {pts}");
        }
        asked.rate(500).unwrap();
        asked.keyframe();
        assert!(!asked
            .encode(&mut y, &mut u, &mut v, 64, &mut mb, 3)
            .unwrap()
            .is_empty());
    }
}
