-- 0010_node_purpose_constraints.sql: purpose 枚举与 primary-ordinary 约束（B15）。
-- 背景：0009 只加了列；DB 层无枚举约束、primary 允许 video 节点。
-- 注意：本迁移先在隔离库验证；生产 GZ 应用须由二进制迁移器登记（schema_migrations），
--       禁止手工 psql 绕过登记（2026-11-17 gz-api 拒启事故教训）。
UPDATE nodes SET purpose='ordinary' WHERE purpose IS NULL OR purpose='';
ALTER TABLE nodes DROP CONSTRAINT IF EXISTS chk_node_purpose;
ALTER TABLE nodes ADD CONSTRAINT chk_node_purpose CHECK (purpose IN ('ordinary','video'));

CREATE OR REPLACE FUNCTION routermgmt_assert_primary_ordinary() RETURNS trigger AS $$
BEGIN
    IF NEW.is_primary THEN
        IF (SELECT purpose FROM nodes WHERE id = NEW.node_id) <> 'ordinary' THEN
            RAISE EXCEPTION 'primary 绑定必须 purpose=ordinary (node_id=%)', NEW.node_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_primary_ordinary ON node_assignments;
CREATE CONSTRAINT TRIGGER trg_primary_ordinary
    AFTER INSERT OR UPDATE OF node_id, is_primary ON node_assignments
    DEFERRABLE INITIALLY IMMEDIATE
    FOR EACH ROW EXECUTE FUNCTION routermgmt_assert_primary_ordinary();
