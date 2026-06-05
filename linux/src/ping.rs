use std::net::UdpSocket;
use std::time::{Duration, Instant};
use tokio::net::TcpStream;
use tokio::time::timeout;

pub async fn ping_host(hostname: &str) -> i64 {
    let ports = [443, 80];

    for port in ports {
        let address = format!("{}:{}", hostname, port);
        let start = Instant::now();

        // Try to establish TCP connection with 2 second timeout
        match timeout(Duration::from_secs(2), TcpStream::connect(&address)).await {
            Ok(Ok(_)) => {
                // Connection successful, return latency
                return start.elapsed().as_millis() as i64;
            }
            Ok(Err(_)) => {
                // Connection failed, try next port
                continue;
            }
            Err(_) => {
                // Timeout, try next port
                continue;
            }
        }
    }

    // All connection attempts failed
    -1
}

pub async fn ping_gamelift_fleet(ping_host: &str) -> bool {
    let host = ping_host.to_string();
    tokio::task::spawn_blocking(move || {
        let socket = match UdpSocket::bind("0.0.0.0:0") {
            Ok(s) => s,
            Err(_) => return false,
        };

        let _ = socket.set_read_timeout(Some(Duration::from_millis(1500)));
        let _ = socket.set_write_timeout(Some(Duration::from_millis(1500)));

        // GameLift ping protocol: "GLPL" magic (4 bytes) + timestamp (8 bytes, network byte order)
        let mut packet = [0u8; 12];
        packet[0] = 0x47; // G
        packet[1] = 0x4C; // L
        packet[2] = 0x50; // P
        packet[3] = 0x4C; // L

        let timestamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64;
        packet[4] = (timestamp >> 56) as u8;
        packet[5] = (timestamp >> 48) as u8;
        packet[6] = (timestamp >> 40) as u8;
        packet[7] = (timestamp >> 32) as u8;
        packet[8] = (timestamp >> 24) as u8;
        packet[9] = (timestamp >> 16) as u8;
        packet[10] = (timestamp >> 8) as u8;
        packet[11] = timestamp as u8;

        let addr = format!("{}:443", host);
        match socket.send_to(&packet, &addr) {
            Ok(_) => {}
            Err(_) => return false,
        }

        let mut buf = [0u8; 32];
        match socket.recv_from(&mut buf) {
            Ok((size, _)) => {
                // Valid echo: at least 12 bytes starting with "GLPL"
                size >= 12
                    && buf[0] == 0x47
                    && buf[1] == 0x4C
                    && buf[2] == 0x50
                    && buf[3] == 0x4C
            }
            Err(_) => false,
        }
    })
    .await
    .unwrap_or(false)
}
