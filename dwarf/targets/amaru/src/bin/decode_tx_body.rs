//! amaru-cbor-decode-tx-body shim.
//!
//! Reads CBOR bytes from stdin, attempts to decode as `amaru_kernel::TransactionBody`,
//! emits OK/ERR per the Dwarf shim outcome contract. Crash (panic, signal, non-0/1
//! exit) is a finding candidate; Dwarf classifies it as `crash`.

use amaru_kernel::{TransactionBody, from_cbor_no_leftovers};
use std::io::{self, Read};
use std::process::ExitCode;

fn main() -> ExitCode {
    let mut buf = Vec::new();
    if io::stdin().read_to_end(&mut buf).is_err() {
        println!("ERR stdin read failed");
        return ExitCode::from(1);
    }
    match from_cbor_no_leftovers::<TransactionBody>(&buf) {
        Ok(_body) => {
            println!("OK");
            ExitCode::SUCCESS
        }
        Err(e) => {
            println!("ERR {}", e);
            ExitCode::from(1)
        }
    }
}
