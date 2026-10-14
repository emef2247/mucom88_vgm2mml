// Diagnostic data generation only; no VGM conversion or playback.
use std::{env, fs};
use soundlog::mdx::document::MdxDocument;
use soundlog::mdx::command::{MdxCommand, MdxEndOfTrack, MdxPcmMode};
use soundlog::mdx::pdx::{PdxBuilder, PdxDocument};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 4 { return Err("MODE INPUT OUTPUT required".into()); }
    let bytes = fs::read(&args[2])?;
    let out = match args[1].as_str() {
        "mdx9" | "mdx16" => {
            let mut doc = MdxDocument::parse(&bytes)?;
            if doc.tracks.len() != 9 { return Err("Requires nine-track input".into()); }
            let original = doc.clone();
            if args[1] == "mdx16" {
                doc.tracks.resize(16, vec![MdxCommand::EndOfTrack(MdxEndOfTrack)]);
                doc.tracks[0].insert(0, MdxCommand::PcmMode(MdxPcmMode));
            }
            let result = doc.to_bytes()?;
            let mut check = MdxDocument::parse(&result)?;
            if args[1] == "mdx16" {
                assert!(matches!(check.tracks[0].remove(0), MdxCommand::PcmMode(_)));
                for track in &check.tracks[9..] {
                    assert_eq!(track, &vec![MdxCommand::EndOfTrack(MdxEndOfTrack)]);
                }
                check.tracks.truncate(9);
            }
            assert_eq!(check.tracks, original.tracks);
            assert_eq!(check.tone_bank, original.tone_bank);
            assert_eq!(check.header.title, original.header.title);
            assert_eq!(check.header.pdx_name, original.header.pdx_name);
            println!("Typed commands/tones/title/PDX reference preserved; {}", args[1]);
            result
        }
        "pdx" => {
            let original = PdxDocument::parse(&bytes)?;
            let result = PdxBuilder::from_document(&original)?.finalize().to_bytes();
            let check = PdxDocument::parse(&result)?;
            assert_eq!(original.banks.len(), check.banks.len());
            for bank in 0..original.banks.len() {
                for slot in 0..96 {
                    assert_eq!(original.sample_bytes(bank, slot), check.sample_bytes(bank, slot));
                }
            }
            println!("All {} banks and bank/slot payload bindings preserved", original.banks.len());
            result
        }
        _ => return Err("Unknown mode".into()),
    };
    fs::write(&args[3], out)?;
    Ok(())
}
