//! Single path component validation.
//!
//! [`Name`] is the only way to name a child. It is validated with one portable
//! rule set on every OS, so a name accepted on Linux is also accepted on
//! Windows/macOS and means the same single entry. The rules are deliberately
//! stricter than any single OS requires (fail closed):
//!
//! * not empty, not `.` / `..`, at most 255 UTF-16 code units;
//! * no `/`, `\`, NUL, C0 controls or DEL;
//! * no `:` (drive prefixes, NTFS alternate data streams `name:stream:$DATA`);
//! * none of `< > " | ? *` (Win32 wildcards / reserved);
//! * no trailing space or dot (Win32 silently strips them: `a.` would alias `a`);
//! * no leading space (ambiguous in shells and UIs);
//! * not a DOS device name (`CON`, `PRN`, `AUX`, `NUL`, `COM0-9`, `LPT0-9`,
//!   including superscript digits, `CONIN$`, `CONOUT$`), with or without an
//!   extension, case-insensitively.
//!
//! Unicode is kept as given (no normalization). Two names that differ only by
//! normalization or case may still collide on case/normalization-insensitive
//! file systems; exclusive create reports that as [`ErrorCode::AlreadyExists`].
//!
//! Existing ancestor directories walked by [`crate::DirChain::open_absolute`]
//! are validated with the OS rules instead (see `sys::validate_existing_component`).

use crate::error::{Error, ErrorCode, Result};

/// Maximum length in UTF-16 code units (NTFS/APFS limit; ext4 limit is 255 bytes,
/// checked separately below).
const MAX_UTF16_UNITS: usize = 255;
const MAX_UTF8_BYTES: usize = 255;

/// A validated single path component.
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct Name(String);

impl std::fmt::Debug for Name {
    // Names may be private (user file names); never print them by default.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Name(<redacted>)")
    }
}

impl Name {
    /// Validate `s` as a single portable component.
    pub fn new(s: &str) -> Result<Self> {
        validate_portable(s)?;
        Ok(Self(s.to_owned()))
    }

    /// The component as text.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

fn invalid() -> Error {
    Error::new(ErrorCode::InvalidName)
}

pub(crate) fn validate_portable(s: &str) -> Result<()> {
    if s.len() > MAX_UTF8_BYTES {
        return Err(invalid());
    }
    validate_win32_rules(s)
}

/// Win32 ambiguity rules with the NTFS length limit only (255 UTF-16 units). Used for
/// *existing* Windows components, which may legitimately exceed 255 UTF-8 bytes.
pub(crate) fn validate_win32_rules(s: &str) -> Result<()> {
    if s.is_empty() || s == "." || s == ".." {
        return Err(invalid());
    }
    if s.encode_utf16().count() > MAX_UTF16_UNITS {
        return Err(invalid());
    }
    for c in s.chars() {
        let bad = matches!(c, '/' | '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*')
            || (c as u32) < 0x20
            || c == '\u{7f}';
        if bad {
            return Err(invalid());
        }
    }
    if s.ends_with(' ') || s.ends_with('.') || s.starts_with(' ') {
        return Err(invalid());
    }
    if is_dos_device_name(s) {
        return Err(invalid());
    }
    Ok(())
}

/// DOS device names are reserved in every directory on Win32, with any extension.
pub(crate) fn is_dos_device_name(s: &str) -> bool {
    // The device part is everything before the first '.', trailing spaces trimmed
    // (Win32 treats "NUL .txt" as NUL).
    let stem = s.split('.').next().unwrap_or(s).trim_end_matches(' ');
    let upper = stem.to_uppercase();
    match upper.as_str() {
        "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$" => true,
        _ => {
            let mut chars = upper.chars();
            let prefix: String = chars.by_ref().take(3).collect();
            let rest: Vec<char> = chars.collect();
            (prefix == "COM" || prefix == "LPT")
                && rest.len() == 1
                && rest
                    .first()
                    .is_some_and(|d| d.is_ascii_digit() || matches!(d, '¹' | '²' | '³'))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_ordinary_names() {
        for ok in [
            "a",
            "report.srt",
            ".utteran-reexport",
            ".git",
            "日本語のフォルダ",
            "x y",
            "COM10",
            "CONSOLE",
            "LPT",
            "a~1",
            "e\u{301}", // decomposed é is kept as-is
        ] {
            assert!(Name::new(ok).is_ok(), "should accept {ok:?}");
        }
    }

    #[test]
    fn rejects_ambiguous_or_multi_component_names() {
        let long = "a".repeat(256);
        for bad in [
            "",
            ".",
            "..",
            "a/b",
            "a\\b",
            "/abs",
            "C:",
            "C:x",
            "file:stream",
            "file::$DATA",
            "nul\0byte",
            "tab\tname",
            "del\u{7f}",
            "trailing.",
            "trailing ",
            " leading",
            "wild*",
            "q?",
            "pipe|",
            "lt<",
            "gt>",
            "quote\"",
            "CON",
            "con",
            "Con.txt",
            "NUL .txt",
            "nul.tar.gz",
            "AUX",
            "PRN",
            "COM1",
            "com9.log",
            "LPT0",
            "COM¹",
            "LPT³.txt",
            "CONIN$",
            "conout$",
            long.as_str(),
        ] {
            assert_eq!(
                Name::new(bad).map(|_| ()),
                Err(Error::new(ErrorCode::InvalidName)),
                "should reject {bad:?}"
            );
        }
    }

    #[test]
    fn length_counts_utf16_and_bytes() {
        // 85 * 3-byte chars = 255 bytes: accepted. 86 -> 258 bytes: rejected.
        assert!(Name::new(&"あ".repeat(85)).is_ok());
        assert!(Name::new(&"あ".repeat(86)).is_err());
    }

    #[test]
    fn debug_never_prints_the_name() {
        let n = Name::new("private-name").ok();
        assert_eq!(format!("{n:?}"), "Some(Name(<redacted>))");
    }
}
