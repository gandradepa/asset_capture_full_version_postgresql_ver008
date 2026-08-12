-- C4: recreate the Asset_System_info view (Dashboard prereq). NOT brought by ETL/c3; was a live
-- one-off during Dashboard QA -> a fresh ETL+migrate would lose it. master.*+system.* have dup
-- column names (illegal in PG) -> explicit m_/s_ aliases (matches what the Dashboard reads). Idempotent.
CREATE OR REPLACE VIEW "Asset_System_info" AS
 SELECT master."Code", master."Description" AS "m_Description", master."Status",
    master."User-Defined Type", master."Asset Group", master."Asset Group Name" AS "m_Asset Group Name",
    master."Asset Tag", master."Property code", master."Property Name" AS "m_Property Name",
    master."Space Number", master."Space Name", master."Space Details", master."Main Asset Code",
    master."Main Asset Description", master."External QR Code", system."Asset Code",
    system."Description" AS "s_Description", system."Property.Property code",
    system."Property Name" AS "s_Property Name", system."Asset Group Name" AS "s_Asset Group Name",
    system."Asset Group.Classification code", system."Asset Group.Classification group", system."Asset Type"
   FROM "UBC - Asset Data Master Info" master
     LEFT JOIN "SUST - System List" system ON master."Main Asset Code" = system."Asset Code";
