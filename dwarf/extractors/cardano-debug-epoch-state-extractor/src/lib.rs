use std::path::{Path, PathBuf};

use anyhow::{Result, bail};
use clap::Parser;
use pallas_codec::minicbor;
use pallas_codec::utils::{Bytes, TagWrap};
use pallas_network::{facades::NodeClient, miniprotocols::localstate::queries_v16::{BlockQuery, Datum, LedgerQuery, Request}};
use serde::Serialize;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub socket: PathBuf,
    pub network_magic: u64,
    pub era: u16,
    pub out: PathBuf,
    pub debug_raw_response: Option<PathBuf>,
    pub result_json: Option<PathBuf>,
}

#[derive(Debug, Parser)]
#[command(name = "cardano-debug-epoch-state-extractor")]
struct Cli {
    #[arg(long)]
    socket: PathBuf,
    #[arg(long)]
    network_magic: u64,
    #[arg(long)]
    era: String,
    #[arg(long, default_value = "debug-epoch-state")]
    query: String,
    #[arg(long)]
    out: PathBuf,
    #[arg(long)]
    debug_raw_response: Option<PathBuf>,
    #[arg(long)]
    result_json: Option<PathBuf>,
}

pub fn parse_config_from<I, T>(args: I) -> Result<Config>
where
    I: IntoIterator<Item = T>,
    T: Into<std::ffi::OsString> + Clone,
{
    let cli = Cli::parse_from(args);
    if cli.query != "debug-epoch-state" {
        bail!("unsupported query {}", cli.query);
    }
    let era = parse_era(&cli.era)?;

    Ok(Config {
        socket: cli.socket,
        network_magic: cli.network_magic,
        era,
        out: cli.out,
        debug_raw_response: cli.debug_raw_response,
        result_json: cli.result_json,
    })
}

pub fn parse_era(value: &str) -> Result<u16> {
    match value {
        "conway" => Ok(7),
        _ => value
            .parse::<u16>()
            .map_err(|err| anyhow::anyhow!("invalid era value {value}: {err}")),
    }
}

pub fn flatten_cbor_chunks(chunks: &[TagWrap<Bytes, 24>]) -> Result<Vec<u8>> {
    if chunks.is_empty() {
        bail!("no cbor chunks returned by node");
    }

    let total = chunks.iter().map(|chunk| chunk.len()).sum();
    let mut out = Vec::with_capacity(total);
    for chunk in chunks {
        out.extend_from_slice(chunk.as_slice());
    }

    Ok(out)
}

pub fn write_snapshot_file(out: &Path, bytes: &[u8]) -> Result<()> {
    std::fs::write(out, bytes)?;
    Ok(())
}

pub fn write_raw_response_file(out: &Path, bytes: &[u8]) -> Result<()> {
    std::fs::write(out, bytes)?;
    Ok(())
}

pub fn decode_cbor_chunks_response(bytes: &[u8]) -> Result<Vec<TagWrap<Bytes, 24>>> {
    if let Ok(chunks) = minicbor::decode(bytes) {
        return Ok(chunks);
    }

    if let Ok((chunks,)) = minicbor::decode::<(Vec<TagWrap<Bytes, 24>>,)>(bytes) {
        return Ok(chunks);
    }

    let datums: Vec<Datum> = minicbor::decode(bytes)?;
    Ok(datums.into_iter().map(|(_, chunk)| chunk).collect())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractionSummary {
    pub era: u16,
    pub chunk_count: usize,
    pub byte_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExtractionResult {
    pub snapshot_path: PathBuf,
    pub snapshot_size: usize,
    pub snapshot_sha256: String,
    pub raw_response_path: Option<PathBuf>,
    pub socket_path: PathBuf,
    pub network_magic: u64,
    pub era: u16,
    pub exit_status: String,
}

#[derive(Debug, Serialize)]
struct SerializableExtractionResult {
    snapshot_path: String,
    snapshot_size: usize,
    snapshot_sha256: String,
    raw_response_path: Option<String>,
    socket_path: String,
    network_magic: u64,
    era: u16,
    exit_status: String,
}

pub fn write_result_json_file(out: &Path, result: &ExtractionResult) -> Result<()> {
    let payload = SerializableExtractionResult {
        snapshot_path: result.snapshot_path.to_string_lossy().into_owned(),
        snapshot_size: result.snapshot_size,
        snapshot_sha256: result.snapshot_sha256.clone(),
        raw_response_path: result
            .raw_response_path
            .as_ref()
            .map(|p| p.to_string_lossy().into_owned()),
        socket_path: result.socket_path.to_string_lossy().into_owned(),
        network_magic: result.network_magic,
        era: result.era,
        exit_status: result.exit_status.clone(),
    };
    let json = serde_json::to_vec(&payload)?;
    std::fs::write(out, json)?;
    Ok(())
}

pub fn build_extraction_result(config: &Config, summary: &ExtractionSummary) -> Result<ExtractionResult> {
    let snapshot_bytes = std::fs::read(&config.out)?;
    let snapshot_sha256 = sha256_hex(&snapshot_bytes);
    Ok(ExtractionResult {
        snapshot_path: config.out.clone(),
        snapshot_size: summary.byte_count,
        snapshot_sha256,
        raw_response_path: config.debug_raw_response.clone(),
        socket_path: config.socket.clone(),
        network_magic: config.network_magic,
        era: summary.era,
        exit_status: "ok".to_string(),
    })
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    hex::encode(hasher.finalize())
}

pub async fn extract_debug_epoch_state(config: &Config) -> Result<ExtractionSummary> {
    let mut client = NodeClient::connect(&config.socket, config.network_magic).await?;
    client.statequery().acquire(None).await?;

    let request = Request::LedgerQuery(LedgerQuery::BlockQuery(
        config.era,
        BlockQuery::GetCBOR(Box::new(BlockQuery::DebugEpochState)),
    ));
    let response = client
        .statequery()
        .query_any(pallas_codec::utils::AnyCbor::from_encode(request))
        .await?;
    if let Some(path) = &config.debug_raw_response {
        write_raw_response_file(path, response.as_ref())?;
    }
    let chunks = decode_cbor_chunks_response(response.as_ref())?;
    let bytes = flatten_cbor_chunks(&chunks)?;
    write_snapshot_file(&config.out, &bytes)?;

    let _ = client.statequery().send_release().await;
    client.abort().await;

    Ok(ExtractionSummary {
        era: config.era,
        chunk_count: chunks.len(),
        byte_count: bytes.len(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunk(bytes: &[u8]) -> TagWrap<Bytes, 24> {
        TagWrap::new(Bytes::from(bytes.to_vec()))
    }

    #[test]
    fn parses_numeric_era_from_cli() {
        let cfg = parse_config_from([
            "extractor",
            "--socket",
            "/tmp/node.sock",
            "--network-magic",
            "42",
            "--era",
            "8",
            "--out",
            "/tmp/state.cbor",
        ])
        .expect("config should parse");

        assert_eq!(cfg.era, 8);
        assert_eq!(cfg.network_magic, 42);
        assert_eq!(cfg.socket, PathBuf::from("/tmp/node.sock"));
        assert_eq!(cfg.out, PathBuf::from("/tmp/state.cbor"));
        assert_eq!(cfg.debug_raw_response, None);
    }

    #[test]
    fn parses_conway_alias_to_battle_tested_query_era() {
        let era = parse_era("conway").expect("alias should parse");
        assert_eq!(era, 7);
    }

    #[test]
    fn parses_optional_raw_response_path_from_cli() {
        let cfg = parse_config_from([
            "extractor",
            "--socket",
            "/tmp/node.sock",
            "--network-magic",
            "42",
            "--era",
            "7",
            "--out",
            "/tmp/state.cbor",
            "--debug-raw-response",
            "/tmp/raw.bin",
        ])
        .expect("config should parse");

        assert_eq!(cfg.debug_raw_response, Some(PathBuf::from("/tmp/raw.bin")));
    }

    #[test]
    fn parses_optional_result_json_path_from_cli() {
        let cfg = parse_config_from([
            "extractor",
            "--socket",
            "/tmp/node.sock",
            "--network-magic",
            "42",
            "--era",
            "7",
            "--out",
            "/tmp/state.cbor",
            "--result-json",
            "/tmp/result.json",
        ])
        .expect("config should parse");

        assert_eq!(cfg.result_json, Some(PathBuf::from("/tmp/result.json")));
    }

    #[test]
    fn flattens_cbor_chunks_by_concatenating_inner_bytes() {
        let flattened = flatten_cbor_chunks(&[chunk(&[0x82, 0x00]), chunk(&[0x81, 0x01])])
            .expect("flattening should succeed");

        assert_eq!(flattened, vec![0x82, 0x00, 0x81, 0x01]);
    }

    #[test]
    fn writes_flattened_snapshot_bytes_to_requested_path() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("state.cbor");

        write_snapshot_file(&out, &[0x82, 0x00, 0x81, 0x01]).expect("write should succeed");

        let bytes = std::fs::read(out).expect("snapshot file should exist");
        assert_eq!(bytes, vec![0x82, 0x00, 0x81, 0x01]);
    }

    #[test]
    fn writes_raw_response_bytes_to_requested_path() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("raw.bin");

        write_raw_response_file(&out, &[0x83, 0x07, 0x64, 0x74]).expect("write should succeed");

        let bytes = std::fs::read(out).expect("raw file should exist");
        assert_eq!(bytes, vec![0x83, 0x07, 0x64, 0x74]);
    }

    #[test]
    fn writes_result_json_with_expected_fields() {
        let dir = tempfile::tempdir().expect("tempdir");
        let out = dir.path().join("result.json");
        let result = ExtractionResult {
            snapshot_path: PathBuf::from("/tmp/state.cbor"),
            snapshot_size: 10509,
            snapshot_sha256: "deadbeef".to_string(),
            raw_response_path: Some(PathBuf::from("/tmp/raw.bin")),
            socket_path: PathBuf::from("/tmp/node.sock"),
            network_magic: 42,
            era: 6,
            exit_status: "ok".to_string(),
        };

        write_result_json_file(&out, &result).expect("write should succeed");

        let written =
            std::fs::read_to_string(out).expect("result json file should exist and be readable");
        assert!(written.contains("\"snapshot_path\":\"/tmp/state.cbor\""));
        assert!(written.contains("\"snapshot_size\":10509"));
        assert!(written.contains("\"snapshot_sha256\":\"deadbeef\""));
        assert!(written.contains("\"raw_response_path\":\"/tmp/raw.bin\""));
        assert!(written.contains("\"socket_path\":\"/tmp/node.sock\""));
        assert!(written.contains("\"network_magic\":42"));
        assert!(written.contains("\"era\":6"));
        assert!(written.contains("\"exit_status\":\"ok\""));
    }

    #[test]
    fn builds_result_from_config_summary_and_written_snapshot() {
        let dir = tempfile::tempdir().expect("tempdir");
        let snapshot = dir.path().join("state.cbor");
        write_snapshot_file(&snapshot, &[0x82, 0x00, 0x81, 0x01]).expect("snapshot write");
        let cfg = Config {
            socket: PathBuf::from("/tmp/node.sock"),
            network_magic: 42,
            era: 6,
            out: snapshot.clone(),
            debug_raw_response: Some(PathBuf::from("/tmp/raw.bin")),
            result_json: Some(PathBuf::from("/tmp/result.json")),
        };
        let summary = ExtractionSummary {
            era: 6,
            chunk_count: 1,
            byte_count: 4,
        };

        let result = build_extraction_result(&cfg, &summary).expect("result build");

        assert_eq!(result.snapshot_path, snapshot);
        assert_eq!(result.snapshot_size, 4);
        assert_eq!(result.raw_response_path, Some(PathBuf::from("/tmp/raw.bin")));
        assert_eq!(result.socket_path, PathBuf::from("/tmp/node.sock"));
        assert_eq!(result.network_magic, 42);
        assert_eq!(result.era, 6);
        assert_eq!(result.exit_status, "ok");
        assert_eq!(
            result.snapshot_sha256,
            "79fe9b64703b4e8c55a762225f015ec49b29fc5a60fca65d0a346615cffd155b"
        );
    }

    #[test]
    fn decodes_tuple_wrapped_cbor_chunk_response() {
        let expected = vec![chunk(&[0x82, 0x00]), chunk(&[0x81, 0x01])];
        let encoded = minicbor::to_vec((expected.clone(),)).expect("encode");

        let decoded = decode_cbor_chunks_response(&encoded).expect("decode should succeed");

        assert_eq!(decoded, expected);
    }

    #[test]
    fn decodes_datum_wrapped_cbor_chunk_response() {
        let expected = vec![chunk(&[0x82, 0x00]), chunk(&[0x81, 0x01])];
        let encoded =
            minicbor::to_vec(vec![(7u16, expected[0].clone()), (7u16, expected[1].clone())])
                .expect("encode");

        let decoded = decode_cbor_chunks_response(&encoded).expect("decode should succeed");

        assert_eq!(decoded, expected);
    }
}
