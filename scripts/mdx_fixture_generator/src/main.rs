// SPDX-License-Identifier: MIT
// External MDX/PDX construction, fixture-generation and replay utility.
use std::{collections::HashSet, env, fs, path::{Component, Path, PathBuf}, fmt::Write};
use soundlog::mdx::{convert::{to_vgm_document, MdxToVgmOptions}, package::MdxPackage};
use soundlog::mdx::pdx::{PdxBuilder, PdxDocument};

type Error = Box<dyn std::error::Error>;

fn parent_directory(path: &Path) -> &Path {
    path.parent().filter(|parent| !parent.as_os_str().is_empty()).unwrap_or(Path::new("."))
}

fn relative_file(directory: &Path, name: &str) -> Result<PathBuf, Error> {
    let relative = Path::new(name);
    if relative.as_os_str().is_empty() || relative.components().any(|c| !matches!(c, Component::Normal(_))) {
        return Err(format!("Expected a nonempty relative file path: {name}").into());
    }
    let root = directory.canonicalize()?;
    let file = root.join(relative).canonicalize()?;
    if !file.starts_with(&root) || !file.is_file() {
        return Err(format!("File leaves its input directory: {name}").into());
    }
    Ok(file)
}

fn build_pdx(manifest: &Path) -> Result<Vec<u8>, Error> {
    let text = fs::read_to_string(manifest)?;
    let mut lines = text.lines();
    if lines.next() != Some("sample_id\tbank\tslot\tfile") {
        return Err("PDX manifest must start with sample_id<TAB>bank<TAB>slot<TAB>file".into());
    }
    let directory = parent_directory(manifest);
    let mut builder = PdxBuilder::new();
    let mut slots = HashSet::new();
    let mut identities = HashSet::new();
    let mut total = 96u64 * 8;
    for (index, line) in lines.enumerate() {
        let fields: Vec<&str> = line.split('\t').collect();
        if fields.len() != 4 || fields.iter().any(|field| field.is_empty()) {
            return Err(format!("Invalid PDX manifest row {}", index + 2).into());
        }
        let bank: usize = fields[1].parse()?;
        let slot: usize = fields[2].parse()?;
        if bank != 0 || slot >= 96 {
            return Err("PDX generation supports only bank 0 and slots 0..95".into());
        }
        if !slots.insert(slot) || !identities.insert(fields[0].to_owned()) {
            return Err("Duplicate PDX slot or sample_id".into());
        }
        let file = relative_file(directory, fields[3])?;
        let size = fs::metadata(&file)?.len();
        if size == 0 || size > 0x00ff_ffff {
            return Err("PDX sample must contain 1..0x00ffffff encoded bytes".into());
        }
        total = total.checked_add(size).ok_or("PDX offset overflow")?;
        if total > u32::MAX as u64 { return Err("PDX offset exceeds u32".into()); }
        let bytes = fs::read(file)?;
        if bytes.len() as u64 != size { return Err("PDX sample changed while reading".into()); }
        builder.set_sample(bank, slot, bytes)?;
    }
    if slots.is_empty() { return Err("PDX manifest has no samples".into()); }
    Ok(builder.finalize().to_bytes())
}

fn write_pdx_atomic(manifest: &Path, output: &Path) -> Result<(), Error> {
    if let Ok(existing) = output.canonicalize() {
        if manifest.canonicalize().ok().as_ref() == Some(&existing) {
            return Err("PDX output must not replace its manifest".into());
        }
        if let Ok(text) = fs::read_to_string(manifest) {
            for line in text.lines().skip(1) {
                let fields: Vec<&str> = line.split('\t').collect();
                if fields.len() == 4 && parent_directory(manifest).join(fields[3]).canonicalize().ok().as_ref() == Some(&existing) {
                    return Err("PDX output must not replace an input sample".into());
                }
            }
        }
    }
    // Invalidate the previous product even when validation fails.
    if output.exists() { fs::remove_file(output)?; }
    let bytes = build_pdx(manifest)?;
    let parent = parent_directory(output);
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(".{}-{}.tmp", output.file_name().unwrap().to_string_lossy(), std::process::id()));
    let mut created = false;
    let result = (|| -> Result<(), Error> {
        use std::io::Write;
        let mut file = fs::OpenOptions::new().create_new(true).write(true).open(&temporary)?;
        created = true;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fs::rename(&temporary, output)?;
        Ok(())
    })();
    if created && temporary.exists() { let _ = fs::remove_file(temporary); }
    result
}

fn load_pdx(package: &mut MdxPackage, input: &Path) -> Result<Option<PathBuf>, Error> {
    let mut resolved = None;
    if let Some(name) = package.pdx_name().filter(|name| !name.is_empty()).map(str::to_owned) {
        if name.contains(['/', '\\']) || Path::new(&name).components().count() != 1
            || !matches!(Path::new(&name).components().next(), Some(Component::Normal(_))) {
            return Err("PDX name must be a filename beside the input MDX/MML".into());
        }
        let directory = parent_directory(input);
        let exact = directory.join(&name);
        let file = if exact.is_file() {
            relative_file(directory, &name)?
        } else {
            let candidates: Vec<PathBuf> = fs::read_dir(directory)?
                .filter_map(Result::ok)
                .map(|entry| entry.path())
                .filter(|path| path.is_file() && path.file_name().is_some_and(|n| n.to_string_lossy().eq_ignore_ascii_case(&name)))
                .collect();
            match candidates.as_slice() {
                [file] => relative_file(directory, &file.file_name().unwrap().to_string_lossy())?,
                [] => return Err(format!("Missing PDX beside input: {name}").into()),
                _ => return Err(format!("Ambiguous case-insensitive PDX name: {name}").into()),
            }
        };
        package.pdx = Some(PdxDocument::parse(&fs::read(&file)?)?);
        resolved = Some(file);
    }
    for reference in package.pcm_references() {
        if package.pcm_sample_bytes(&reference).is_none_or(|data| data.is_empty()) {
            return Err(format!("Missing PCM sample: track {}, bank {}, slot {}", reference.track, reference.bank, reference.note).into());
        }
    }
    Ok(resolved)
}

fn select_standard_pcm(package: &mut MdxPackage) -> Result<(), Error> {
    use soundlog::mdx::command::MdxCommand;
    if package.mdx.tracks.len() > 9 {
        if package.mdx.tracks.iter().skip(9).flatten().any(|command| !matches!(command, MdxCommand::EndOfTrack(_))) {
            return Err("Standard PCM mode cannot contain active Q..W tracks".into());
        }
        package.mdx.tracks.truncate(9);
        // mmlx/MdxBuilder inserts the extended-layout marker into track A.
        // Selecting the standard layout must remove that marker as well.
        if matches!(package.mdx.tracks[0].first(), Some(MdxCommand::PcmMode(_))) {
            package.mdx.tracks[0].remove(0);
        }
        if package.mdx.tracks.iter().flatten().any(|command| matches!(command, MdxCommand::PcmMode(_))) {
            return Err("Standard PCM mode cannot contain PCM8 mode commands".into());
        }
        // Eager replay reads the header's track count, not tracks.len().
        // Reparse the canonical bytes to keep both representations aligned.
        package.mdx = soundlog::mdx::document::MdxDocument::parse(&package.mdx.to_bytes()?)?;
    }
    Ok(())
}

struct PcmTrackPlan {
    pdx_name: String,
    tempo: u8,
    end_tick: u64,
    song_loop_tick: Option<u64>,
    commands: Vec<soundlog::mdx::command::MdxCommand>,
}

fn parse_pcm_track(text: &str) -> Result<PcmTrackPlan, Error> {
    use soundlog::mdx::command::*;
    let rows: Vec<Vec<&str>> = text.lines().map(|line| line.split('\t').collect()).collect();
    if rows.first().map(Vec::as_slice) != Some(["kind", "value", "ticks"].as_slice())
        || rows.len() < 5 || rows.iter().any(|row| row.len() != 3) {
        return Err("PCM track plan requires kind<TAB>value<TAB>ticks and three-column rows".into());
    }
    for (index, key) in ["pdx_name", "tempo", "end_tick"].iter().enumerate() {
        if rows[index + 1][0] != *key || rows[index + 1][1].is_empty() || !rows[index + 1][2].is_empty() {
            return Err(format!("PCM track metadata must begin pdx_name, tempo, end_tick: {key}").into());
        }
    }
    let tempo: u8 = rows[2][1].parse()?;
    let end_tick: u64 = rows[3][1].parse()?;
    if end_tick == 0 || end_tick > u32::MAX as u64 { return Err("PCM end_tick must be 1..u32::MAX".into()); }
    let mut commands = Vec::new();
    let mut total = 0u64;
    let mut initial = HashSet::new();
    let mut hold = false;
    let mut ended = false;
    let mut song_loop = None;
    for row in rows.iter().skip(4) {
        if ended { return Err("Commands after PCM end".into()); }
        if hold && row[0] != "note" { return Err("PCM hold must immediately precede a note".into()); }
        match row[0] {
            "bank" | "frequency" | "pan" | "gate" | "volume" => {
                if !row[2].is_empty() { return Err("PCM control must have empty ticks".into()); }
                let value: u8 = row[1].parse()?;
                let command: MdxCommand = match row[0] {
                    "bank" if value == 0 => MdxVoiceOrPcmBank { value }.into(),
                    "frequency" if value <= 4 => MdxAdpcmOrNoiseFrequency { value }.into(),
                    "pan" if value <= 3 => MdxPan::from_raw(value).into(),
                    "gate" if value == 8 => MdxGate { value }.into(),
                    "volume" if value == 128 => MdxVolume { value }.into(),
                    _ => return Err(format!("Unsupported standard PCM control {}={value}", row[0]).into()),
                };
                initial.insert(row[0]);
                commands.push(command);
            }
            "hold" => {
                if !row[1].is_empty() || !row[2].is_empty() { return Err("PCM hold operands must be empty".into()); }
                commands.push(MdxKeyOffDisable.into());
                hold = true;
            }
            "note" => {
                if initial.len() != 5 { return Err("PCM note requires explicit bank, frequency, pan, gate and volume".into()); }
                let slot: u8 = row[1].parse()?;
                let ticks: u16 = row[2].parse()?;
                if slot >= 96 { return Err("PCM slot must be 0..95".into()); }
                commands.push(MdxNote::new(0x80 + slot, ticks).ok_or("PCM note ticks must be 1..256")?.into());
                total = total.checked_add(u64::from(ticks)).ok_or("PCM duration overflow")?;
                hold = false;
            }
            "rest" => {
                if !row[1].is_empty() { return Err("PCM rest value must be empty".into()); }
                let ticks: u16 = row[2].parse()?;
                commands.push(MdxRest::new(ticks).ok_or("PCM rest ticks must be 1..128")?.into());
                total = total.checked_add(u64::from(ticks)).ok_or("PCM duration overflow")?;
            }
            "song_loop" => {
                if !row[1].is_empty() || !row[2].is_empty() || song_loop.is_some() {
                    return Err("PCM song_loop requires empty operands and one marker".into());
                }
                let position: usize = commands.iter().map(|command: &MdxCommand| command.to_mdx_bytes().unwrap().len()).sum();
                song_loop = Some((position, total));
            }
            "end" => {
                if !row[1].is_empty() || !row[2].is_empty() { return Err("PCM end operands must be empty".into()); }
                if let Some((position, tick)) = song_loop {
                    if tick >= total { return Err("PCM song loop must have positive duration".into()); }
                    let length: usize = commands.iter().map(|command: &MdxCommand| command.to_mdx_bytes().unwrap().len()).sum();
                    let offset = i16::try_from(position as i64 - length as i64 - 3)
                        .map_err(|_| "PCM song loop exceeds signed 16-bit offset")?;
                    commands.push(MdxCommand::EndOfTrackLoop(MdxRelativeOffset { opcode: 0xf1, offset }));
                } else {
                    commands.push(MdxEndOfTrack.into());
                }
                ended = true;
            }
            kind => return Err(format!("Unknown PCM command: {kind}").into()),
        }
    }
    if !ended || total != end_tick {
        return Err(format!("PCM plan needs final end and exact end_tick: {total} != {end_tick}").into());
    }
    Ok(PcmTrackPlan { pdx_name: rows[1][1].to_owned(), tempo, end_tick, song_loop_tick: song_loop.map(|(_, tick)| tick), commands })
}

fn fm_track_timing(commands: &[soundlog::mdx::command::MdxCommand], tempo: u8, saw_tempo: &mut bool) -> Result<(u64, Option<u64>), Error> {
    use soundlog::mdx::command::MdxCommand;
    let mut total = 0u64;
    let mut repeats = Vec::new();
    let mut byte_position = 0usize;
    let mut boundary_ticks = std::collections::HashMap::new();
    let mut song_loop_tick = None;
    let mut ended = false;
    for command in commands {
        if ended { return Err("Commands after FM track end".into()); }
        if repeats.is_empty() { boundary_ticks.insert(byte_position as i64, total); }
        match command {
            MdxCommand::Note(note) => total = total.checked_add(u64::from(note.length)).ok_or("FM duration overflow")?,
            MdxCommand::Rest(rest) => total = total.checked_add(u64::from(rest.ticks)).ok_or("FM duration overflow")?,
            MdxCommand::Tempo(value) => {
                if value.value != tempo { return Err("FM tempo differs from PCM shared tempo".into()); }
                *saw_tempo = true;
            }
            MdxCommand::LoopStart(start) => {
                if start.count == 0 { return Err("Infinite FM repeat unsupported in direct PCM compilation".into()); }
                repeats.push((total, start.count, byte_position + command.to_mdx_bytes().ok_or("Unserializable FM repeat")?.len(), None, Vec::<i64>::new()));
                total = 0;
            }
            MdxCommand::LoopEnd(end) => {
                let (before, count, body_position, escape_ticks, escape_targets) = repeats.pop().ok_or("FM repeat end without start")?;
                if end.offset as i64 != body_position as i64 - (byte_position + 3) as i64 {
                    return Err("FM repeat offset does not return to its body".into());
                }
                if escape_targets.iter().any(|target| *target != byte_position as i64) {
                    return Err("FM repeat escape does not target its repeat end".into());
                }
                let last = escape_ticks.unwrap_or(total);
                total = total.checked_mul(u64::from(count - 1)).and_then(|body| body.checked_add(last)).and_then(|body| before.checked_add(body)).ok_or("FM repeat duration overflow")?;
            }
            MdxCommand::LoopEscape(escape) => {
                let frame = repeats.last_mut().ok_or("FM repeat escape without start")?;
                frame.3.get_or_insert(total);
                frame.4.push(byte_position as i64 + 2 + i64::from(escape.offset));
            }
            MdxCommand::EndOfTrackLoop(offset) => {
                if !repeats.is_empty() || offset.offset >= 0 { return Err("Invalid FM song loop".into()); }
                let target = byte_position as i64 + 3 + i64::from(offset.offset);
                let tick = *boundary_ticks.get(&target).ok_or("FM song loop does not target an outer command boundary")?;
                if tick >= total { return Err("FM song loop must have positive duration".into()); }
                song_loop_tick = Some(tick);
                ended = true;
            }
            MdxCommand::EndOfTrack(_) => { ended = true; }
            MdxCommand::Jump(_) | MdxCommand::SyncWait(_) | MdxCommand::PcmMode(_) =>
                return Err("Unsupported FM control flow in direct PCM compilation".into()),
            _ => (),
        }
        byte_position += command.to_mdx_bytes().ok_or("Unserializable FM command")?.len();
    }
    if !repeats.is_empty() { return Err("Unterminated FM repeat".into()); }
    Ok((total, song_loop_tick))
}

#[cfg(test)]
fn fm_track_ticks(commands: &[soundlog::mdx::command::MdxCommand], tempo: u8, saw_tempo: &mut bool) -> Result<u64, Error> {
    Ok(fm_track_timing(commands, tempo, saw_tempo)?.0)
}

fn invalidate_pcm_output(fm_input: &Path, plan_input: &Path, output: &Path) -> Result<(), Error> {
    if !output.exists() { return Ok(()); }
    let existing = output.canonicalize()?;
    if fm_input.canonicalize().ok().as_ref() == Some(&existing) || plan_input.canonicalize().ok().as_ref() == Some(&existing) {
        return Err("Direct PCM output must not replace its inputs".into());
    }
    // Protect declared PDX inputs before full plan/package validation, so an
    // invalid plan cannot turn an input alias into a disposable old product.
    if let Ok(text) = fs::read_to_string(plan_input) {
        for line in text.lines() {
            let fields: Vec<&str> = line.split('\t').collect();
            if fields.first() != Some(&"pdx_name") || fields.len() < 2 { continue; }
            let directory = parent_directory(output);
            if directory.join(fields[1]).canonicalize().ok().as_ref() == Some(&existing) {
                return Err("Direct PCM output must not replace its PDX input".into());
            }
            for entry in fs::read_dir(directory)? {
                let entry = entry?;
                if entry.file_name().to_string_lossy().eq_ignore_ascii_case(fields[1]) && entry.path().canonicalize().ok().as_ref() == Some(&existing) {
                    return Err("Direct PCM output must not replace its PDX input".into());
                }
            }
        }
    }
    fs::remove_file(output)?;
    Ok(())
}

fn compile_pcm(fm_input: &Path, plan_input: &Path, output: &Path) -> Result<(), Error> {
    invalidate_pcm_output(fm_input, plan_input, output)?;
    let result = compile_pcm_inner(fm_input, plan_input, output);
    if result.is_err() && output.exists() { fs::remove_file(output)?; }
    result
}

fn compile_pcm_inner(fm_input: &Path, plan_input: &Path, output: &Path) -> Result<(), Error> {
    use soundlog::mdx::{command::{MdxCommand, MdxEndOfTrack}, document::{MdxBuilder, MdxDocument}};
    let plan = parse_pcm_track(&fs::read_to_string(plan_input)?)?;
    let parsed = mmlx::mdx::parse(&fs::read_to_string(fm_input)?)?;
    let fm = mmlx::mdx::compile(&parsed)?;
    if fm.header.pdx_name.as_ref().is_some_and(|name| !name.is_empty())
        || fm.tracks.iter().skip(8).flatten().any(|command| !matches!(command, MdxCommand::EndOfTrack(_)))
        || fm.tracks.iter().flatten().any(|command| matches!(command, MdxCommand::PcmMode(_))) {
        return Err("Direct PCM compilation requires FM-only MML without PDX or PCM tracks".into());
    }
    let mut saw_tempo = false;
    let mut longest = 0;
    for track in fm.tracks.iter().take(8) {
        let (ticks, loop_tick) = fm_track_timing(track, plan.tempo, &mut saw_tempo)?;
        if ticks > 0 && (loop_tick != plan.song_loop_tick || loop_tick.is_some() && ticks != plan.end_tick) {
            return Err("FM/PCM song loop start and end must match on every active track".into());
        }
        if ticks > plan.end_tick { return Err("FM track exceeds shared PCM end_tick".into()); }
        longest = longest.max(ticks);
    }
    if !saw_tempo || longest != plan.end_tick { return Err("FM requires shared tempo and matching maximum end_tick".into()); }
    let mut builder = MdxBuilder::new();
    let title_bytes = fm.header.to_bytes()[..fm.header.title_byte_len()].to_vec();
    builder.set_title_bytes(title_bytes.clone()).set_pdx_name(Some(&plan.pdx_name));
    for tone in &fm.tone_bank.tones { builder.append_tone(tone.clone()); }
    for (index, track) in fm.tracks.iter().take(8).enumerate() { builder.set_track(index, track.clone()); }
    builder.set_track(8, plan.commands.clone());
    // The sixteen-track layout selects the reference-verified PCM extension
    // route. MdxBuilder supplies its initial E8 marker on track A.
    for index in 9..16 { builder.set_track(index, vec![MdxEndOfTrack.into()]); }
    let bytes = builder.finalize()?.to_bytes()?;
    if bytes.len() > 65535 { return Err("Combined native MDX exceeds 65535 bytes".into()); }
    let mut package = MdxPackage { mdx: MdxDocument::parse(&bytes)?, pdx: None };
    if package.mdx.tracks.len() != 16 || package.mdx.header.track_count() != 16 || package.mdx.tracks[8] != plan.commands
        || !matches!(package.mdx.tracks[0].first(), Some(MdxCommand::PcmMode(_)))
        || package.mdx.tracks.iter().flatten().filter(|command| matches!(command, MdxCommand::PcmMode(_))).count() != 1
        || package.mdx.tracks[9..].iter().any(|track| track.as_slice() != [MdxEndOfTrack.into()]) {
        return Err("Serialized direct PCM track/layout differs from plan".into());
    }
    if package.mdx.tracks[0][1..] != fm.tracks[0] || package.mdx.tracks[1..8] != fm.tracks[1..8]
        || package.mdx.tone_bank != fm.tone_bank || package.mdx.header.to_bytes()[..package.mdx.header.title_byte_len()] != title_bytes
        || package.mdx.header.pdx_name.as_deref() != Some(plan.pdx_name.as_str()) {
        return Err("Serialized direct PCM MDX changed FM tracks, tones or title".into());
    }
    fs::create_dir_all(parent_directory(output))?;
    let pdx = load_pdx(&mut package, output)?;
    if let Ok(existing) = output.canonicalize() {
        if pdx.as_ref() == Some(&existing) || fm_input.canonicalize().ok().as_ref() == Some(&existing) || plan_input.canonicalize().ok().as_ref() == Some(&existing) {
            return Err("Direct PCM output must not replace its inputs".into());
        }
    }
    fs::write(output, bytes)?;
    Ok(())
}

fn command_census(package: &MdxPackage) -> Result<String, Error> {
    use soundlog::mdx::command::MdxCommand;
    let mut csv = String::from("track,index,kind,opcode_hex,operands_hex,ticks\n");
    for (track, commands) in package.mdx.tracks.iter().enumerate() {
        let label = if track < 8 { (b'A' + track as u8) as char } else { (b'P' + (track - 8) as u8) as char };
        for (index, command) in commands.iter().enumerate() {
            let (kind, ticks) = match command {
                MdxCommand::Note(note) => ("Note", u64::from(note.length)),
                MdxCommand::Rest(rest) => ("Rest", u64::from(rest.ticks)),
                MdxCommand::KeyOffDisable(_) => ("KeyOffDisable", 0),
                MdxCommand::OpmRegisterWrite(_) => ("OpmRegisterWrite", 0),
                MdxCommand::Tempo(_) => ("Tempo", 0),
                MdxCommand::LoopStart(_) => ("LoopStart", 0),
                MdxCommand::LoopEnd(_) => ("LoopEnd", 0),
                MdxCommand::LoopEscape(_) => ("LoopEscape", 0),
                MdxCommand::EndOfTrackLoop(_) => ("EndOfTrackLoop", 0),
                MdxCommand::Jump(_) => ("Jump", 0),
                MdxCommand::EndOfTrack(_) => ("EndOfTrack", 0),
                MdxCommand::PcmMode(_) => ("PcmMode", 0),
                _ => ("Control", 0),
            };
            let bytes = command.to_mdx_bytes().ok_or("MDX command serialization unavailable")?;
            let opcode = bytes.first().ok_or("Empty MDX command")?;
            let operands: String = bytes.iter().skip(1).map(|byte| format!("{byte:02x}")).collect();
            writeln!(&mut csv, "{label},{index},{kind},{opcode:02x},{operands},{ticks}")?;
        }
    }
    Ok(csv)
}

fn inspect_commands(input: &Path, output: &Path) -> Result<(), Error> {
    if output.canonicalize().ok().as_ref() == Some(&input.canonicalize()?) {
        return Err("Command census output must not replace its MDX input".into());
    }
    let package = MdxPackage::parse(&fs::read(input)?, None)?;
    let csv = command_census(&package)?;
    fs::create_dir_all(parent_directory(output))?;
    fs::write(output, csv)?;
    Ok(())
}

fn run_args(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    if args.get(1).is_some_and(|arg| arg == "--inspect-commands") {
        if args.len() != 4 { return Err("usage: --inspect-commands INPUT.mdx OUTPUT.csv".into()); }
        return inspect_commands(Path::new(&args[2]), Path::new(&args[3]));
    }
    if args.get(1).is_some_and(|arg| arg == "--compile-pcm") {
        if args.len() != 5 { return Err("usage: --compile-pcm FM_ONLY.mml PLAN.tsv OUTPUT.mdx".into()); }
        return compile_pcm(Path::new(&args[2]), Path::new(&args[3]), Path::new(&args[4]));
    }
    if args.get(1).is_some_and(|arg| arg == "--build-pdx") {
        if args.len() != 4 { return Err("usage: --build-pdx MANIFEST.tsv OUTPUT.pdx".into()); }
        return write_pdx_atomic(Path::new(&args[2]), Path::new(&args[3]));
    }
    if args.len() < 4 || (args.len() - 4) % 2 != 0 {
        return Err("usage: mdx-fixture-generator INPUT.mml OUTPUT.mdx OUTPUT.vgm [--max-ticks N] [--loop-count N] [--pcm-mode standard] | --compile-pcm FM_ONLY.mml PLAN.tsv OUTPUT.mdx | --compile-only INPUT.mml OUTPUT.mdx [--pcm-mode standard] | --from-mdx INPUT.mdx OUTPUT.vgm [--max-ticks N] [--loop-count N] | --inspect-mdx INPUT.mdx OUTPUT.csv | --inspect-commands INPUT.mdx OUTPUT.csv | --build-pdx MANIFEST.tsv OUTPUT.pdx".into());
    }
    if args[1] == "--inspect-mdx" {
        if args.len() != 4 { return Err("--inspect-mdx takes only INPUT.mdx OUTPUT.csv".into()); }
        let package = MdxPackage::parse(&fs::read(&args[2])?, None)?;
        let mut csv = String::from("voice_id,algorithm,feedback,op");
        for name in ["m1", "m2", "c1", "c2"] {
            for field in ["ar", "d1r", "d2r", "rr", "d1l", "tl", "ks", "mul", "dt1", "dt2", "am_enabled"] {
                write!(&mut csv, ",{name}_{field}")?;
            }
        }
        csv.push('\n');
        for tone in &package.mdx.tone_bank.tones {
            write!(&mut csv, "{},{},{},{}", tone.voice_number, tone.con, tone.fl, tone.op)?;
            for op in tone.operators {
                write!(&mut csv, ",{},{},{},{},{},{},{},{},{},{},{}",
                       op.ar, op.dr, op.sr, op.rr, op.sl, op.ol, op.ks, op.ml, op.dt1, op.dt2, op.ame)?;
            }
            csv.push('\n');
        }
        if let Some(parent) = Path::new(&args[3]).parent() { fs::create_dir_all(parent)?; }
        fs::write(&args[3], csv)?;
        return Ok(());
    }
    let mut options = MdxToVgmOptions { loop_count: Some(1), ..Default::default() };
    let mut standard_pcm = false;
    let mut selected = HashSet::new();
    for option in args[4..].chunks_exact(2) {
        if !selected.insert(&option[0]) { return Err("Duplicate option".into()); }
        match option[0].as_str() {
            "--max-ticks" => {
                let limit: u32 = option[1].parse()?;
                if limit == 0 { return Err("max-ticks must be positive".into()); }
                options.max_ticks = Some(limit);
            }
            "--loop-count" => {
                let count: u32 = option[1].parse()?;
                if count == 0 { return Err("loop-count must be positive".into()); }
                options.loop_count = Some(count);
            }
            "--pcm-mode" if option[1] == "standard" => standard_pcm = true,
            _ => return Err("Unknown option (PCM mode supports only standard)".into()),
        }
    }
    let from_mdx = args[1] == "--from-mdx";
    let compile_only = args[1] == "--compile-only";
    let input = Path::new(&args[if from_mdx || compile_only { 2 } else { 1 }]);
    let mut package = if from_mdx {
        MdxPackage::parse(&fs::read(&args[2])?, None)?
    } else {
        let text = fs::read_to_string(input)?;
        let parsed = mmlx::mdx::parse(&text)?;
        MdxPackage { mdx: mmlx::mdx::compile(&parsed)?, pdx: None }
    };
    if standard_pcm { select_standard_pcm(&mut package)?; }
    let pdx_input = load_pdx(&mut package, input)?;
    if !from_mdx {
        // Keep a compiled MDX available even if the external replay fails.
        let mdx_bytes = package.to_mdx_bytes()?;
        let output = Path::new(&args[if compile_only { 3 } else { 2 }]);
        fs::create_dir_all(parent_directory(output))?;
        fs::write(output, mdx_bytes)?;
    }
    if compile_only { return Ok(()); }
    if package.drives_okim6258() {
        let output = Path::new(&args[3]);
        if output.exists() {
            let resolved_output = output.canonicalize()?;
            if resolved_output == input.canonicalize()? || (!from_mdx && resolved_output == Path::new(&args[2]).canonicalize()?) || pdx_input.as_ref() == Some(&resolved_output) {
                return Err("PCM replay output must not replace its input, PDX or compiled MDX".into());
            }
            fs::remove_file(output)?;
        }
        return Err("PCM replay unavailable: soundlog 0.15.0 does not preserve legacy ADPCM holds, stops and decoder resets; use --compile-only INPUT.mml OUTPUT.mdx --pcm-mode standard".into());
    }
    let vgm_bytes: Vec<u8> = to_vgm_document(&package, &options)?.into();
    if let Some(parent) = Path::new(&args[3]).parent() { fs::create_dir_all(parent)?; }
    fs::write(&args[3], vgm_bytes)?;
    Ok(())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if let Err(error) = run_args(&args) { eprintln!("{error}"); std::process::exit(1); }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Directory(PathBuf);
    impl Directory {
        fn new() -> Self {
            let path = env::temp_dir().join(format!("x68k-pdx-test-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn manifest(&self, rows: &str) -> PathBuf {
            let path = self.0.join("manifest.tsv");
            fs::write(&path, format!("sample_id\tbank\tslot\tfile\n{rows}")).unwrap();
            path
        }
    }
    impl Drop for Directory { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

    fn package(name: &str, note: usize) -> MdxPackage {
        let parsed = mmlx::mdx::parse(&format!("#title \"PCM test\"\n#pcmfile \"{name}\"\nP @0 F4 n{note},4\n")).unwrap();
        MdxPackage { mdx: mmlx::mdx::compile(&parsed).unwrap(), pdx: None }
    }

    #[test]
    fn command_census_preserves_encoded_events_and_does_not_replace_input() {
        use soundlog::mdx::command::{MdxCommand, MdxNote, MdxRest, MdxKeyOffDisable, MdxOpmRegisterWrite, MdxEndOfTrack, MdxPcmMode};
        let directory = Directory::new();
        let mut source = package("", 0);
        source.mdx.tracks[0] = vec![
            MdxPcmMode.into(),
            MdxOpmRegisterWrite { register: 8, value: 0x78 }.into(),
            MdxKeyOffDisable.into(),
            MdxNote::new(0x80, 256).unwrap().into(),
            MdxRest { ticks: 128 }.into(),
            MdxEndOfTrack.into(),
        ];
        source.mdx.tracks.resize(16, vec![MdxEndOfTrack.into()]);
        source.mdx.tracks[15] = vec![MdxEndOfTrack.into()];
        let input = directory.0.join("input.mdx");
        let bytes = source.to_mdx_bytes().unwrap();
        fs::write(&input, &bytes).unwrap();
        let output = directory.0.join("commands.csv");
        inspect_commands(&input, &output).unwrap();
        let census = fs::read_to_string(&output).unwrap();
        assert!(census.contains("A,0,PcmMode,e8,,0\n"));
        assert!(census.contains("A,1,OpmRegisterWrite,fe,0878,0\n"));
        assert!(census.contains("A,2,KeyOffDisable,f7,,0\n"));
        assert!(census.contains("A,3,Note,80,ff,256\n"));
        assert!(census.contains("A,4,Rest,7f,,128\n"));
        assert!(census.contains("A,5,EndOfTrack,f1,00,0\n"));
        assert!(census.contains("P,"));
        assert!(census.contains("W,"));
        assert!(inspect_commands(&input, &input).is_err());
        assert_eq!(fs::read(&input).unwrap(), bytes);
        assert_eq!(source.mdx.tracks[0].iter().filter(|command| matches!(command, MdxCommand::Note(_))).count(), 1);
    }

    #[test]
    fn encoded_bytes_odd_size_last_slot_and_empty_slot_are_preserved() {
        let directory = Directory::new();
        fs::write(directory.0.join("odd.adpcm"), [0x01, 0xfe, 0x73]).unwrap();
        fs::write(directory.0.join("last.adpcm"), [0x98, 0x76]).unwrap();
        let bytes = build_pdx(&directory.manifest("odd\t0\t0\todd.adpcm\nlast\t0\t95\tlast.adpcm\n")).unwrap();
        // Independent raw table checks, then typed package checks.
        assert_eq!(&bytes[..8], &[0, 0, 3, 0, 0, 0, 0, 3]);
        assert_eq!(&bytes[8..16], &[0; 8]);
        assert_eq!(&bytes[95*8..96*8], &[0, 0, 3, 3, 0, 0, 0, 2]);
        assert_eq!(&bytes[768..], &[0x01, 0xfe, 0x73, 0x98, 0x76]);
        let pdx = PdxDocument::parse(&bytes).unwrap();
        assert_eq!(pdx.sample_bytes(0, 0), Some([0x01, 0xfe, 0x73].as_slice()));
        assert_eq!(pdx.sample_bytes(0, 1), None);
        assert_eq!(pdx.sample_bytes(0, 95), Some([0x98, 0x76].as_slice()));
        assert!(!pdx.is_compressed());
    }

    #[test]
    fn malformed_or_unrepresentable_manifests_fail_without_stale_output() {
        let directory = Directory::new();
        fs::write(directory.0.join("sample.adpcm"), [0x71]).unwrap();
        fs::write(directory.0.join("empty.adpcm"), []).unwrap();
        let large = fs::File::create(directory.0.join("large.adpcm")).unwrap();
        large.set_len(0x0100_0000).unwrap();
        for rows in ["", "a\t0\t96\tsample.adpcm\n", "a\t1\t0\tsample.adpcm\n",
                     "a\t0\t0\tempty.adpcm\n", "a\t0\t0\tmissing.adpcm\n",
                     "a\t0\t0\tlarge.adpcm\n", "a\t0\t0\t../sample.adpcm\n",
                     "a\t0\t0\tsample.adpcm\nb\t0\t0\tsample.adpcm\n",
                     "a\t0\t0\tsample.adpcm\na\t0\t1\tsample.adpcm\n"] {
            let output = directory.0.join("test.pdx");
            fs::write(&output, b"stale").unwrap();
            assert!(write_pdx_atomic(&directory.manifest(rows), &output).is_err(), "{rows}");
            assert!(!output.exists());
        }
    }

    #[test]
    fn pcm_resolution_case_missing_ambiguity_and_empty_references() {
        let directory = Directory::new();
        fs::write(directory.0.join("sample.adpcm"), [0x01, 0xfe, 0x73]).unwrap();
        let bytes = build_pdx(&directory.manifest("a\t0\t0\tsample.adpcm\n")).unwrap();
        let input = directory.0.join("test.mml");
        let mut missing = package("TEST.PDX", 0);
        assert!(load_pdx(&mut missing, &input).unwrap_err().to_string().contains("Missing PDX"));
        fs::write(directory.0.join("test.pdx"), &bytes).unwrap();
        let mut valid = package("TEST.PDX", 0);
        load_pdx(&mut valid, &input).unwrap();
        assert_eq!(valid.pcm_sample_bytes(&valid.pcm_references()[0]), Some([0x01, 0xfe, 0x73].as_slice()));
        let mut empty = package("test.pdx", 1);
        assert!(load_pdx(&mut empty, &input).unwrap_err().to_string().contains("Missing PCM sample"));
        fs::write(directory.0.join("Test.Pdx"), &bytes).unwrap();
        assert!(load_pdx(&mut missing, &input).unwrap_err().to_string().contains("Ambiguous"));
        let mut traversal = package("../test.pdx", 0);
        assert!(load_pdx(&mut traversal, &input).is_err());
    }

    #[test]
    fn pdx_output_cannot_overwrite_manifest_or_encoded_input() {
        let directory = Directory::new();
        let sample = directory.0.join("sample.adpcm");
        fs::write(&sample, [0x37]).unwrap();
        let manifest = directory.manifest("a\t0\t0\tsample.adpcm\n");
        assert!(write_pdx_atomic(&manifest, &manifest).is_err());
        assert!(write_pdx_atomic(&manifest, &sample).is_err());
        assert!(manifest.is_file());
        assert_eq!(fs::read(sample).unwrap(), [0x37]);
    }

    #[test]
    fn standard_pcm_requires_unused_extra_tracks() {
        let mut standard = package("sample.pdx", 0);
        assert_eq!(standard.mdx.tracks.len(), 16);
        select_standard_pcm(&mut standard).unwrap();
        assert_eq!(standard.mdx.tracks.len(), 9);
        assert_eq!(standard.mdx.header.track_count(), 9);
        let reparsed = MdxPackage::parse(&standard.to_mdx_bytes().unwrap(), None).unwrap();
        assert_eq!(reparsed.mdx.tracks.len(), 9);
        let parsed = mmlx::mdx::parse("P n0,4\nQ n0,4\n").unwrap();
        let mut extended = MdxPackage { mdx: mmlx::mdx::compile(&parsed).unwrap(), pdx: None };
        assert!(select_standard_pcm(&mut extended).is_err());
        assert_eq!(extended.mdx.tracks.len(), 16);
    }

    #[test]
    fn pcm_compilation_survives_replay_guard_and_removes_stale_vgm() {
        let directory = Directory::new();
        fs::write(directory.0.join("sample.adpcm"), [0x01, 0xfe, 0x73]).unwrap();
        write_pdx_atomic(&directory.manifest("a\t0\t0\tsample.adpcm\n"), &directory.0.join("test.pdx")).unwrap();
        let input = directory.0.join("test.mml");
        fs::write(&input, "#pcmfile \"test.pdx\"\nP @0 F4 n0,4\n").unwrap();
        let mdx = directory.0.join("test.mdx");
        let vgm = directory.0.join("test.vgm");
        fs::write(&vgm, b"stale").unwrap();
        let args = vec!["helper".into(), input.to_string_lossy().into_owned(), mdx.to_string_lossy().into_owned(), vgm.to_string_lossy().into_owned(), "--pcm-mode".into(), "standard".into()];
        assert!(run_args(&args).unwrap_err().to_string().contains("PCM replay unavailable:"));
        assert!(!vgm.exists());
        assert_eq!(MdxPackage::parse(&fs::read(&mdx).unwrap(), None).unwrap().mdx.tracks.len(), 9);
        let compile = vec!["helper".into(), "--compile-only".into(), input.to_string_lossy().into_owned(), mdx.to_string_lossy().into_owned(), "--pcm-mode".into(), "standard".into()];
        run_args(&compile).unwrap();
        let replay = vec!["helper".into(), "--from-mdx".into(), mdx.to_string_lossy().into_owned(), vgm.to_string_lossy().into_owned()];
        assert!(run_args(&replay).unwrap_err().to_string().contains("PCM replay unavailable:"));
        assert!(!vgm.exists());
    }

    #[test]
    fn pinned_replay_cannot_preserve_raw_holds_or_per_note_resets() {
        fn replay(track: &str, raw: &[u8]) -> (Vec<u8>, Vec<u8>) {
            let parsed = mmlx::mdx::parse(&format!("#pcmfile \"test.pdx\"\nA @t255 r%800\nP @0 F4 {track}\n")).unwrap();
            let mut builder = PdxBuilder::new();
            builder.set_sample(0, 0, raw.to_vec()).unwrap();
            let mut package = MdxPackage { mdx: mmlx::mdx::compile(&parsed).unwrap(), pdx: Some(builder.finalize()) };
            select_standard_pcm(&mut package).unwrap();
            let bytes: Vec<u8> = to_vgm_document(&package, &MdxToVgmOptions { max_ticks: Some(1000), loop_count: Some(1), ..Default::default() }).unwrap().into();
            let mut position = 0x34 + u32::from_le_bytes(bytes[0x34..0x38].try_into().unwrap()) as usize;
            let mut payload = Vec::new();
            let mut controls = Vec::new();
            loop {
                match bytes[position] {
                    0xb7 => {
                        if bytes[position + 1] == 1 { payload.push(bytes[position + 2]); }
                        if bytes[position + 1] == 0 { controls.push(bytes[position + 2]); }
                        position += 3;
                    }
                    0x54 | 0x61 => position += 3,
                    0x62 | 0x63 => position += 1,
                    0x66 => break,
                    command => panic!("unexpected synthesized command {command:x}"),
                }
            }
            (payload, controls)
        }
        let raw: Vec<u8> = (0..4096).map(|i| i as u8).collect();
        let (held, _) = replay("n0,%255 & n0,%255 & n0,%255", &raw);
        let prefix = held.iter().zip(&raw).take_while(|(actual, expected)| actual == expected).count();
        println!("pinned standard replay: held raw prefix {prefix}/{} bytes", held.len());
        assert!(prefix > 100 && prefix < 1530, "raw continuation unexpectedly changed: {prefix}/{}", held.len());
        let (_, controls) = replay("n0,%32 r%32 n0,%32", &raw);
        println!("pinned standard replay: two separated notes have controls {controls:?}");
        assert_eq!(controls, [2, 1], "pinned converter unexpectedly gained per-note STOP/PLAY");
    }

    #[test]
    fn compact_pcm_finite_repeat_preserves_ordered_note_and_hold_commands() {
        use soundlog::mdx::command::MdxCommand;
        fn expand(commands: &[MdxCommand], position: &mut usize, nested: bool) -> Vec<MdxCommand> {
            let mut result = Vec::new();
            while let Some(command) = commands.get(*position) {
                *position += 1;
                match command {
                    MdxCommand::LoopStart(start) => {
                        assert!(start.count > 0, "only finite repeats belong in this test");
                        let body_start = *position;
                        let body = expand(commands, position, true);
                        let body_bytes: usize = commands[body_start..*position - 1].iter()
                            .map(|command| command.to_mdx_bytes().unwrap().len()).sum();
                        let MdxCommand::LoopEnd(end) = &commands[*position - 1] else { unreachable!() };
                        assert_eq!(end.offset as i32, -(body_bytes as i32 + 3));
                        for _ in 0..start.count { result.extend(body.clone()); }
                    }
                    MdxCommand::LoopEnd(_) => {
                        assert!(nested, "unexpected repeat end");
                        return result;
                    }
                    MdxCommand::LoopEscape(_) | MdxCommand::Jump(_) | MdxCommand::EndOfTrackLoop(_) =>
                        panic!("unexpected control flow in finite-repeat witness"),
                    command => result.push(command.clone()),
                }
            }
            assert!(!nested, "unterminated repeat");
            result
        }
        fn compiled(track: &str) -> Vec<MdxCommand> {
            let parsed = mmlx::mdx::parse(&format!("P @0 F4 p1 q8 @v127 {track}\n")).unwrap();
            let mut package = MdxPackage { mdx: mmlx::mdx::compile(&parsed).unwrap(), pdx: None };
            select_standard_pcm(&mut package).unwrap();
            let mut position = 0;
            expand(&package.mdx.tracks[8], &mut position, false)
        }
        let plain = compiled(&("n95,1 & ".repeat(8) + "n95,8"));
        let compact = compiled("[n95,1 &]8 n95,8");
        // Equality includes ordered bank, frequency, pan, gate, volume,
        // KeyOffDisable and typed Note(slot, ticks), not merely total time.
        assert_eq!(compact, plain);
        assert_eq!(compact.iter().filter(|command| matches!(command, MdxCommand::Note(_))).count(), 9);
        assert_eq!(compact.iter().filter(|command| matches!(command, MdxCommand::KeyOffDisable(_))).count(), 8);
    }

    fn pcm_plan_rows(end_tick: u64, body: &str) -> String {
        format!("kind\tvalue\tticks\npdx_name\ttest.pdx\t\ntempo\t255\t\nend_tick\t{end_tick}\t\nbank\t0\t\nfrequency\t4\t\npan\t1\t\ngate\t8\t\nvolume\t128\t\n{body}end\t\t\n")
    }

    #[test]
    fn direct_pcm_compilation_uses_plan_and_preserves_fm_without_pcm_mml() {
        use soundlog::mdx::command::MdxCommand;
        let directory = Directory::new();
        let inputs = directory.0.join("passes");
        fs::create_dir(&inputs).unwrap();
        let fm_path = inputs.join("fm.mml");
        let fm_text = "#title \"直接PCM\"\n@0 = {31,0,0,15,0,127,0,1,0,0,0,31,0,0,15,0,127,0,1,0,0,0,31,0,0,15,0,127,0,1,0,0,0,31,0,0,15,0,24,0,1,0,0,0,0,7,15}\nA @t255 @0 p3 @v96 q8 o4 [c%64 r%64]2\nB p2 r%256\n";
        fs::write(&fm_path, fm_text).unwrap();
        let plan_path = inputs.join("plan.tsv");
        fs::write(&plan_path, pcm_plan_rows(256, "hold\t\t\nnote\t95\t128\npan\t2\t\nnote\t95\t128\n")).unwrap();
        let mut pdx_builder = PdxBuilder::new();
        pdx_builder.set_sample(0, 95, vec![0x37, 0xfe, 0x01]).unwrap();
        let pdx_bytes = pdx_builder.finalize().to_bytes();
        fs::write(directory.0.join("test.pdx"), &pdx_bytes).unwrap();
        let output = directory.0.join("combined.mdx");
        run_args(&["helper".into(), "--compile-pcm".into(), fm_path.to_string_lossy().into_owned(), plan_path.to_string_lossy().into_owned(), output.to_string_lossy().into_owned()]).unwrap();
        let result = MdxPackage::parse(&fs::read(output).unwrap(), Some(&pdx_bytes)).unwrap();
        let fm = mmlx::mdx::compile(&mmlx::mdx::parse(fm_text).unwrap()).unwrap();
        assert_eq!(result.mdx.tracks.len(), 16);
        assert_eq!(result.mdx.header.track_count(), 16);
        assert!(matches!(result.mdx.tracks[0].first(), Some(MdxCommand::PcmMode(_))));
        assert_eq!(result.mdx.tracks.iter().flatten().filter(|command| matches!(command, MdxCommand::PcmMode(_))).count(), 1);
        assert_eq!(result.mdx.tracks[0][1..], fm.tracks[0]);
        assert_eq!(result.mdx.tracks[1..8], fm.tracks[1..8]);
        assert!(result.mdx.tracks[9..].iter().all(|track| matches!(track.as_slice(), [MdxCommand::EndOfTrack(_)])));
        assert_eq!(result.mdx.tracks[8], parse_pcm_track(&fs::read_to_string(&plan_path).unwrap()).unwrap().commands);
        assert_eq!(result.mdx.tone_bank, fm.tone_bank);
        assert_eq!(result.mdx.tone_bank.tones.len(), 1);
        assert_eq!(result.mdx.header.pdx_name.as_deref(), Some("test.pdx"));
        assert_eq!(&result.mdx.header.to_bytes()[..result.mdx.header.title_byte_len()], &fm.header.to_bytes()[..fm.header.title_byte_len()]);
        assert!(matches!(result.mdx.tracks[8][5], MdxCommand::KeyOffDisable(_)));
        assert!(matches!(result.mdx.tracks[8][6], MdxCommand::Note(note) if note.note == 0xdf && note.length == 128));
        assert_eq!(result.pcm_sample_bytes(&result.pcm_references()[0]), Some([0x37, 0xfe, 0x01].as_slice()));
        assert_eq!(fs::read(directory.0.join("test.pdx")).unwrap(), pdx_bytes);
        assert!(!directory.0.join("combined.vgm").exists());
    }

    #[test]
    fn replay_accepts_positive_loop_count_and_rejects_zero() {
        let directory = Directory::new();
        let input = directory.0.join("loop.mml");
        let mdx = directory.0.join("loop.mdx");
        let vgm = directory.0.join("loop.vgm");
        fs::write(&input, "A @t255 r%2 L r%4\n").unwrap();
        let args = vec!["helper".into(), input.to_string_lossy().into_owned(),
                        mdx.to_string_lossy().into_owned(), vgm.to_string_lossy().into_owned(),
                        "--loop-count".into(), "3".into(), "--max-ticks".into(), "100".into()];
        run_args(&args).unwrap();
        assert!(vgm.exists());
        let mut invalid = args.clone();
        invalid[5] = "0".into();
        assert!(run_args(&invalid).unwrap_err().to_string().contains("loop-count must be positive"));
    }

    #[test]
    fn direct_pcm_song_loop_encodes_shared_positive_boundary() {
        let plan = "kind\tvalue\tticks\npdx_name\ttest.pdx\t\ntempo\t255\t\nend_tick\t6\t\nrest\t\t2\nsong_loop\t\t\nrest\t\t4\nend\t\t\n";
        let pcm = parse_pcm_track(plan).unwrap();
        assert_eq!(pcm.song_loop_tick, Some(2));
        let mut saw_tempo = false;
        assert_eq!(fm_track_timing(&pcm.commands, 255, &mut saw_tempo).unwrap(), (6, Some(2)));
        assert!(parse_pcm_track(&plan.replace("rest\t\t4\n", "").replace("end_tick\t6", "end_tick\t2")).is_err());
        assert!(parse_pcm_track(&plan.replace("song_loop\t\t\n", "song_loop\t\t\nsong_loop\t\t\n")).is_err());
    }

    #[test]
    fn direct_pcm_accepts_rest_only_after_intentional_note_omission() {
        let plan = "kind\tvalue\tticks\npdx_name\ttest.pdx\t\ntempo\t191\t\nend_tick\t2\t\nrest\t\t2\nend\t\t\n";
        let parsed = parse_pcm_track(plan).unwrap();
        assert_eq!(parsed.end_tick, 2);
        assert!(matches!(parsed.commands[0], soundlog::mdx::command::MdxCommand::Rest(_)));
        assert!(matches!(parsed.commands[1], soundlog::mdx::command::MdxCommand::EndOfTrack(_)));
        assert!(parse_pcm_track(&plan.replace("rest\t\t2", "rest\t\t1")).is_err());
    }

    #[test]
    fn direct_pcm_rejects_malformed_bounds_order_clock_and_missing_references() {
        let valid = pcm_plan_rows(256, "note\t95\t256\n");
        for malformed in [valid.replace("\t95\t", "\t96\t"), valid.replace("\t256\n", "\t257\n"),
                          valid.replace("volume\t128", "volume\t127"), valid.replace("bank\t0", "bank\t1"),
                          valid.replace("frequency\t4", "frequency\t5"), valid.replace("end_tick\t256", "end_tick\t255"),
                          valid.replace("note\t95\t256", "hold\t\t"), valid.replace("end\t\t\n", ""),
                          valid.clone() + "pan\t1\t\n"] {
            assert!(parse_pcm_track(&malformed).is_err(), "{malformed}");
        }
        let directory = Directory::new();
        let fm = directory.0.join("fm.mml");
        let plan = directory.0.join("plan.tsv");
        let output = directory.0.join("out.mdx");
        fs::write(&plan, &valid).unwrap();
        fs::write(&fm, "A @t254 r%256\n").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).unwrap_err().to_string().contains("tempo"));
        fs::write(&fm, "A @t255 r%255\n").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).unwrap_err().to_string().contains("end_tick"));
        fs::write(&fm, "A @t255 r%256\nP n0,4\n").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).unwrap_err().to_string().contains("FM-only"));
        fs::write(&fm, "A @t255 r%256\n").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).unwrap_err().to_string().contains("Missing PDX"));
        let mut builder = PdxBuilder::new();
        builder.set_sample(0, 0, vec![0x31]).unwrap();
        fs::write(directory.0.join("test.pdx"), builder.finalize().to_bytes()).unwrap();
        assert!(compile_pcm(&fm, &plan, &output).unwrap_err().to_string().contains("Missing PCM sample"));
        assert!(!output.exists());
    }

    #[test]
    fn direct_pcm_fm_duration_counts_nested_finite_escapes() {
        let fm = mmlx::mdx::compile(&mmlx::mdx::parse("A @t255 [[r%10 / r%5]2 r%7]3\n").unwrap()).unwrap();
        let mut saw_tempo = false;
        assert_eq!(fm_track_ticks(&fm.tracks[0], 255, &mut saw_tempo).unwrap(), 96);
        assert!(saw_tempo);
    }

    #[test]
    fn direct_pcm_failure_invalidates_old_output_but_preserves_input_aliases() {
        let directory = Directory::new();
        let fm = directory.0.join("fm.mml");
        let plan = directory.0.join("plan.tsv");
        let pdx = directory.0.join("test.pdx");
        let output = directory.0.join("out.mdx");
        fs::write(&fm, "A @t255 r%256\n").unwrap();
        let valid = pcm_plan_rows(256, "note\t0\t256\n");
        fs::write(&plan, &valid).unwrap();
        let mut builder = PdxBuilder::new();
        builder.set_sample(0, 0, vec![0x37]).unwrap();
        fs::write(&pdx, builder.finalize().to_bytes()).unwrap();
        for input in [&fm, &plan, &pdx] {
            let before = fs::read(input).unwrap();
            assert!(compile_pcm(&fm, &plan, input).is_err());
            assert_eq!(fs::read(input).unwrap(), before);
        }
        // A malformed plan still protects the declared encoded sample input.
        fs::write(&plan, valid.replace("volume\t128", "volume\t127")).unwrap();
        let before = fs::read(&pdx).unwrap();
        assert!(compile_pcm(&fm, &plan, &pdx).is_err());
        assert_eq!(fs::read(&pdx).unwrap(), before);
        fs::write(&output, b"stale MDX").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).is_err());
        assert!(!output.exists());
        fs::write(&plan, valid).unwrap();
        fs::remove_file(&pdx).unwrap();
        fs::write(&output, b"stale MDX").unwrap();
        assert!(compile_pcm(&fm, &plan, &output).is_err());
        assert!(!output.exists());
    }
}
