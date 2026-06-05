use cardano_debug_epoch_state_extractor::{
    build_extraction_result,
    extract_debug_epoch_state,
    parse_config_from,
    write_result_json_file,
};

#[tokio::main]
async fn main() {
    let config = match parse_config_from(std::env::args_os()) {
        Ok(config) => config,
        Err(err) => {
            eprintln!("{err:#}");
            std::process::exit(2);
        }
    };

    match extract_debug_epoch_state(&config).await {
        Ok(summary) => {
            if let Some(path) = &config.result_json {
                match build_extraction_result(&config, &summary).and_then(|result| write_result_json_file(path, &result))
                {
                    Ok(()) => {}
                    Err(err) => {
                        eprintln!("{err:#}");
                        std::process::exit(1);
                    }
                }
            }
            println!(
                "wrote_debug_epoch_state era={} chunks={} bytes={} out={}",
                summary.era,
                summary.chunk_count,
                summary.byte_count,
                config.out.display()
            );
        }
        Err(err) => {
            eprintln!("{err:#}");
            std::process::exit(1);
        }
    }
}
