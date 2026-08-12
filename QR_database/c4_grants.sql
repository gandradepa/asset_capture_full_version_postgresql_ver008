-- C4: grant app-role least-privilege access incl. VIEWS. c3 §7 granted table privileges, but the
-- 4 views are created later by c4 -> assetcap_app had NO view access (all prior staging ran as the
-- superuser, masking this). Run LAST (after all tables+views exist). ALTER DEFAULT PRIVILEGES future-proofs.
GRANT USAGE ON SCHEMA public TO assetcap_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO assetcap_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO assetcap_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO assetcap_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO assetcap_app;
