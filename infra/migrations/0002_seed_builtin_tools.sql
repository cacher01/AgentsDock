-- Seed the versioned built-in tool catalog.
insert into tool_definitions (
    id, tool_key, version, adapter_type,
    input_schema, output_schema, risk_level
)
values
    ('tool_current_time_v1', 'current_time', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_calculator_v1', 'calculator', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_date_calculator_v1', 'date_calculator', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_unit_converter_v1', 'unit_converter', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_query_planner_v1', 'query_planner', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_citation_checker_v1', 'citation_checker', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_result_critic_v1', 'result_critic', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_artifact_writer_v1', 'artifact_writer', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'medium'),
    ('tool_artifact_archiver_v1', 'artifact_archiver', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'medium'),
    ('tool_weather_v1', 'weather', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'low'),
    ('tool_web_search_v1', 'web_search', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'medium'),
    ('tool_web_fetch_v1', 'web_fetch', '1.0.0', 'python_builtin', '{"type":"object"}', '{"type":"object"}', 'medium')
on conflict (tool_key, version) do nothing;
