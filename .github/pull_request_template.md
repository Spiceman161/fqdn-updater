## Summary

-

## Verification

- [ ] `./scripts/verify.sh`

## Safety Checklist

- [ ] No real `config.json`, `.env*`, `secrets/`, or router credentials are committed.
- [ ] RCI/HTTP details stay behind infrastructure adapters.
- [ ] Apply paths remain managed-only and read-before-write.
- [ ] Operator docs are updated when workflows change.
