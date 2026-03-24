# Configuration Changes

## Summary
Removed hardcoded credentials, updated defaults for industry-ready deployment.

## Changes Made

### config.yaml
- `api_key`: Changed from real key `fc795abcba4tct6d4a7b9c8fbf89t7ftedta9f16` to placeholder `access key`
- `secret_key`: Changed from real key `5d9a2d86a7a2f22657b163d78516e31e62daef7d` to placeholder `secret key`
- `endpoint_url`: Already set to `https://api.datonis.io:443` (no change needed)

### core/models.py — CloudConfig
- `endpoint_url` default: `"https://cloud.example.com"` → `"https://api.datonis.io:443"`
- `api_key` default: `""` → `"access key"` (placeholder)
- `secret_key` default: `""` → `"secret key"` (placeholder)

### settings.html
- Access Key and Secret Key input fields show empty when values are the placeholders
- Placeholder text guides user: "Enter Datonis Access Key" / "Enter Datonis Secret Key"
- Keys are only updated server-side if the form field is non-empty (preserving existing keys)

## Impact
- Fresh installations will have no real credentials — user must enter keys via Settings UI
- Existing installations with keys already in config.yaml will continue to work (the settings route only updates keys if non-empty values are submitted)
