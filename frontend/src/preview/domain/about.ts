/**
 * "About Utteran" facts for the mock Help dialog. Only verified facts: the version in
 * pyproject.toml (an UNRELEASED development version of this unmerged prototype PR, not a
 * distribution), the repository's LICENSE (MIT) and the official GitHub repository. No other
 * website is claimed.
 */
const repository = "https://github.com/zawa356/Utteran";

export const ABOUT = {
  name: "Utteran",
  version: "0.1.43",
  license: "MIT License",
  repository,
  licenseUrl: `${repository}/blob/main/LICENSE`,
  noticesUrl: `${repository}/blob/main/THIRD_PARTY_NOTICES.md`,
} as const;
