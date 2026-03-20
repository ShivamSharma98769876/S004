# User Auth & Admin Approval Migration

Run this migration to add email/password login and admin approval for Paper/Live trade types.

**Option 1: Run standalone (if DB already has core schema)**
```bash
psql $DATABASE_URL -f migrations/user_auth_approval_schema.sql
```

**Option 2: Run full bundle**
```bash
psql $DATABASE_URL -f migrations/migration_bundle.sql
```

After migration:
- Existing ADMIN users get `approved_paper=true` and `approved_live=true` automatically.
- New users are created by Admin via **Users** screen (email + password).
- Users log in with email + password (or username for legacy users like admin/trader1).
- Admin approves Paper and/or Live for each user in the **Users** screen.
