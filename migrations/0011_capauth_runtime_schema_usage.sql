-- sklegal:up
GRANT USAGE ON SCHEMA sklegal_identity TO sklegal_runtime;

-- sklegal:down
REVOKE USAGE ON SCHEMA sklegal_identity FROM sklegal_runtime;
