-- sklegal:up
GRANT USAGE ON SCHEMA sklegal_legal TO sklegal_runtime;

-- sklegal:down
REVOKE USAGE ON SCHEMA sklegal_legal FROM sklegal_runtime;
