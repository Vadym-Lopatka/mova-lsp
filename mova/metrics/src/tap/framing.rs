//! LSP stream framing: splits a byte stream into message bodies; resyncs after a gap.

pub struct Framer {
    buf: Vec<u8>,
    skipping: bool,
}

const HDR_MAX: usize = 64 * 1024;
const CL: &[u8] = b"Content-Length:";

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn content_length(hdr: &[u8]) -> Option<usize> {
    for line in hdr.split(|&b| b == b'\n') {
        let Ok(line) = std::str::from_utf8(line) else { continue };
        let line = line.trim();
        if line.len() > 15 && line.is_char_boundary(15) && line[..15].eq_ignore_ascii_case("content-length:") {
            return line[15..].trim().parse().ok();
        }
    }
    None
}

impl Framer {
    pub fn new() -> Self {
        Framer { buf: Vec::new(), skipping: false }
    }

    /// Feeds a chunk; complete bodies go to `out`. Returns the number of resyncs (gaps or bad headers).
    pub fn feed(&mut self, data: &[u8], gap: bool, out: &mut Vec<Vec<u8>>) -> u32 {
        let mut resyncs = 0;
        if gap {
            self.buf.clear();
            self.skipping = true;
            resyncs += 1;
        }
        self.buf.extend_from_slice(data);
        let mut pos = 0;
        loop {
            let rest = &self.buf[pos..];
            if self.skipping {
                match find(rest, CL) {
                    Some(i) => {
                        pos += i;
                        self.skipping = false;
                    }
                    None => {
                        pos = self.buf.len().saturating_sub(CL.len() - 1);
                        break;
                    }
                }
                continue;
            }
            let Some(h) = find(rest, b"\r\n\r\n") else {
                if rest.len() > HDR_MAX {
                    self.skipping = true;
                    resyncs += 1;
                    pos = self.buf.len().saturating_sub(CL.len() - 1);
                }
                break;
            };
            let Some(len) = content_length(&rest[..h]) else {
                self.skipping = true;
                resyncs += 1;
                pos += h + 4;
                continue;
            };
            let total = h + 4 + len;
            if rest.len() < total {
                break;
            }
            out.push(rest[h + 4..total].to_vec());
            pos += total;
        }
        self.buf.drain(..pos);
        resyncs
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_and_resyncs() {
        let mut f = Framer::new();
        let mut out = vec![];
        let m = b"Content-Length: 2\r\n\r\n{}Content-Length: 2\r\n\r\n[]";
        for b in m.chunks(5) {
            f.feed(b, false, &mut out);
        }
        assert_eq!(out, vec![b"{}".to_vec(), b"[]".to_vec()]);
        out.clear();
        let n = f.feed(b"junk more Content-Length: 2\r\n\r\n{}", true, &mut out);
        assert_eq!(n, 1);
        assert_eq!(out, vec![b"{}".to_vec()]);
    }
}
