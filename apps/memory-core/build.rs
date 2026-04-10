fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .compile(
            &["proto/memory.proto", "proto/embedding.proto"],
            &["proto"],
        )?;
    Ok(())
}
