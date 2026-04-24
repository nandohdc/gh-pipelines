# Security Policy

## Reporting A Vulnerability

Please do not open public issues for suspected vulnerabilities.

Use one of these private channels:

1. GitHub private vulnerability report:
   - https://github.com/nandohdc/gh-pipelines/security/advisories/new
2. Email:
   - nandohdc@users.noreply.github.com

When reporting, include:

- affected workflow/action and pinned version (`@v1` or `@v1.x.y`)
- impact and severity estimate
- minimal reproduction steps or proof-of-concept
- any known mitigations

## Supported Versions

`gh-pipelines` follows a floating major support policy:

- `v1` line: actively maintained and receives fixes
- older majors: unsupported

## Sonar Credential Handling (Doppler-backed bundles)

The `ci-with-sonar-*` bundle workflows fetch `SONAR_TOKEN` and `SONAR_HOST_URL` from Doppler at runtime.
Consumer repositories should store only a read-only `DOPPLER_TOKEN` secret.

Rotation and leak response policy:

1. Rotate each consumer repository `DOPPLER_TOKEN` at least every 90 days.
2. If a `DOPPLER_TOKEN` is exposed, immediately revoke and re-issue that token in Doppler.
3. If Sonar credentials themselves are exposed, rotate `SONAR_TOKEN` in SonarQube and update Doppler `sonar/prd`.
