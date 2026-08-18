// Replicates amaru_kernel::Transaction's shape: array-encoded struct (no #[cbor(map)])
// with required fields 0..2 and a trailing Option field #[n(3)].
#[derive(minicbor::Encode, minicbor::Decode, Debug, PartialEq)]
struct TxLike {
    #[n(0)] body: u16,
    #[n(1)] witnesses: u16,
    #[n(2)] is_valid: bool,
    #[n(3)] auxiliary_data: Option<u16>,
}

fn main() {
    // ENCODE with auxiliary_data = None (the null-aux case)
    let t = TxLike { body: 1, witnesses: 2, is_valid: true, auxiliary_data: None };
    let enc = minicbor::to_vec(&t).unwrap();
    println!("encode(None):  header=0x{:02x}  bytes={:02x?}", enc[0], enc);
    println!("  -> {}", if enc[0]==0x83 {"ARRAY(3): Amaru RE-EMITS non-canonical 3-elem => relays bytes cardano-node rejects"}
                        else if enc[0]==0x84 {"ARRAY(4): Amaru normalizes canonically (explicit null) => propagation safe"}
                        else {"other"});

    // ENCODE with auxiliary_data = Some
    let t2 = TxLike { body: 1, witnesses: 2, is_valid: true, auxiliary_data: Some(9) };
    let enc2 = minicbor::to_vec(&t2).unwrap();
    println!("encode(Some):  header=0x{:02x}  bytes={:02x?}", enc2[0], enc2);

    // DECODE a 3-element array (0x83) — should succeed with aux=None (the finding)
    let three: Vec<u8> = vec![0x83, 0x01, 0x02, 0xf5];
    match minicbor::decode::<TxLike>(&three) {
        Ok(v) => println!("decode(0x83…): OK -> {:?}", v),
        Err(e) => println!("decode(0x83…): ERR {e}"),
    }
    // DECODE a 4-element array with explicit null aux (0x84 … 0xf6)
    let four: Vec<u8> = vec![0x84, 0x01, 0x02, 0xf5, 0xf6];
    match minicbor::decode::<TxLike>(&four) {
        Ok(v) => println!("decode(0x84…f6): OK -> {:?}", v),
        Err(e) => println!("decode(0x84…f6): ERR {e}"),
    }
}
