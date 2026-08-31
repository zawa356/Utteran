# Japanese ASR benchmark fixture

`japanese_reference.wav` is a synthetic Japanese reading generated once with the Windows
Japanese system voice `Microsoft Haruka Desktop`. The committed WAV, rather than the locally
installed voice, is the benchmark input, so repeated measurements use identical PCM samples.
`japanese_reference.txt` is its exact reference text and is discovered automatically by the
benchmark command when the WAV is selected. CER normalization applies NFKC, case folding, and
removes whitespace/punctuation only.

The fixture contains no recording or identifying information. Regeneration is optional and may
produce different audio after an operating-system voice update:

```powershell
./tools/generate_benchmark_fixture.ps1
```
