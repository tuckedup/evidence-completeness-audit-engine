-- Parameterize :limit in the caller. AACT is refreshed daily; persist snapshot metadata.
SELECT
    s.nct_id,
    s.enrollment,
    s.overall_status,
    oa.p_value,
    oa.param_type,
    oa.method
FROM studies AS s
JOIN outcome_analyses AS oa ON oa.nct_id = s.nct_id
WHERE s.study_type = 'INTERVENTIONAL'
  AND s.overall_status = 'COMPLETED'
  AND s.enrollment > 0
  AND EXISTS (
      SELECT 1
      FROM design_groups AS dg
      WHERE dg.nct_id = s.nct_id
        AND dg.group_type ILIKE '%random%'
  )
ORDER BY s.nct_id
LIMIT :limit;

