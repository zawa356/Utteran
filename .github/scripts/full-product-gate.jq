# Keep these IDs in sync with full-product-gate.needs in ci.yml.
def required_jobs:
  [
    "linux-lock-schema",
    "linux-lint",
    "linux-types",
    "linux-tests",
    "windows-tests",
    "windows-powershell",
    "contracts-python-3-11",
    "contracts-python-3-12",
    "powershell-bom",
    "privacy-worktree",
    "privacy-history-delta",
    "ui3-frontend-windows",
    "frontend-quality",
    "frontend-bundle-wheel",
    "npm-production-audit",
    "product-e2e-1",
    "product-e2e-2",
    "product-e2e-3"
  ];

def full_product_gate:
  type == "object" and
  keys == (required_jobs | sort) and
  all(.[]; .result == "success");
