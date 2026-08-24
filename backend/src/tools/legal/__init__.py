"""Tools for lawyer or judge style professional roles — 纯刑事。"""

# ── 通用工具（保留）────────────────────────────────────────────
from .case_retrieval_tool import (
    CASE_RETRIEVAL_TOOL_NAME,
    LocalCaseRetrievalEngine,
    create_case_retrieval_tool,
    create_case_search_function,
)
from .citation_check_tool import (
    CITATION_CHECK_TOOL_NAME,
    CitationCheckTool,
    create_citation_check_tool,
)
from .benchmark_eval_tool import (
    BENCHMARK_EVAL_TOOL_NAME,
    BenchmarkEvalTool,
    create_benchmark_eval_tool,
)
from .document_compare_tool import (
    DOCUMENT_COMPARE_TOOL_NAME,
    DocumentCompareTool,
    create_document_compare_tool,
)
from .document_drafting_registry import (
    create_document_drafting_tool_for_scenario,
    extract_document_drafting_tool_payload,
    get_document_drafting_result_field,
    get_document_drafting_tool_name,
    get_document_type_for_scenario,
    normalize_document_drafting_payload,
    normalize_document_drafting_type,
    render_document_drafting_payload,
    render_document_drafting_payload_for_output_dir,
)
from .judgment_drafting_registry import (
    create_judgment_document_tool_for_scenario,
    extract_judgment_document_tool_payload,
    get_judgment_document_tool_name,
    get_judgment_document_type_for_scenario,
    normalize_judgment_document_payload,
    normalize_judgment_document_type,
    render_judgment_document_payload,
)
from .save_lawyer_memory_tool import create_save_lawyer_memory_tool, normalize_lawyer_memory
from .load_lawyer_memory_tool import create_load_lawyer_memory_tool
from .yuandian_law_tool import (
    YUANDIAN_CASE_TOOL_NAME,
    YUANDIAN_LAW_DETAIL_TOOL_NAME,
    YUANDIAN_LAW_TOOL_NAME,
    create_yuandian_case_tool,
    create_yuandian_law_detail_tool,
    create_yuandian_law_tool,
    search_yuandian_case,
    search_yuandian_law,
    search_yuandian_law_detail,
)

# ── 刑事工具 ────────────────────────────────────────────────────
from .indictment_drafting_tool import (
    INDICTMENT_DOCUMENT_TYPE,
    INDICTMENT_PDF_FILENAME,
    INDICTMENT_RESULT_FIELD,
    INDICTMENT_TOOL_NAME,
    IndictmentDraftingTool,
    create_indictment_drafting_tool,
)
from .defense_opinion_drafting_tool import (
    DEFENSE_OPINION_DOCUMENT_TYPE,
    DEFENSE_OPINION_PDF_FILENAME,
    DEFENSE_OPINION_RESULT_FIELD,
    DEFENSE_OPINION_TOOL_NAME,
    DefenseOpinionDraftingTool,
    create_defense_opinion_drafting_tool,
)
from .public_prosecution_tool import (
    PUBLIC_PROSECUTION_DOCUMENT_TYPE,
    PUBLIC_PROSECUTION_PDF_FILENAME,
    PUBLIC_PROSECUTION_RESULT_FIELD,
    PUBLIC_PROSECUTION_TOOL_NAME,
    PublicProsecutionTool,
    create_public_prosecution_drafting_tool,
)
from .criminal_first_instance_judgment_drafting_tool import (
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_PDF_FILENAME,
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_RESULT_FIELD,
    CRIMINAL_FIRST_INSTANCE_JUDGMENT_TOOL_NAME,
    CriminalFirstInstanceJudgmentDraftingTool,
    create_first_instance_criminal_judgment_drafting_tool,
)
from .criminal_second_instance_judgment_drafting_tool import (
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE,
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_PDF_FILENAME,
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_RESULT_FIELD,
    CRIMINAL_SECOND_INSTANCE_JUDGMENT_TOOL_NAME,
    CriminalSecondInstanceJudgmentDraftingTool,
    create_second_instance_criminal_judgment_drafting_tool,
)

__all__ = [
    # 通用
    "BENCHMARK_EVAL_TOOL_NAME",
    "CASE_RETRIEVAL_TOOL_NAME",
    "CITATION_CHECK_TOOL_NAME",
    "DOCUMENT_COMPARE_TOOL_NAME",
    "LocalCaseRetrievalEngine",
    "BenchmarkEvalTool",
    "CitationCheckTool",
    "DocumentCompareTool",
    "create_benchmark_eval_tool",
    "create_case_retrieval_tool",
    "create_case_search_function",
    "create_citation_check_tool",
    "create_document_drafting_tool_for_scenario",
    "create_document_compare_tool",
    "create_judgment_document_tool_for_scenario",
    "create_save_lawyer_memory_tool",
    "create_load_lawyer_memory_tool",
    "extract_document_drafting_tool_payload",
    "extract_judgment_document_tool_payload",
    "get_document_drafting_result_field",
    "get_document_drafting_tool_name",
    "get_document_type_for_scenario",
    "get_judgment_document_tool_name",
    "get_judgment_document_type_for_scenario",
    "normalize_document_drafting_payload",
    "normalize_document_drafting_type",
    "normalize_judgment_document_payload",
    "normalize_judgment_document_type",
    "normalize_lawyer_memory",
    "render_document_drafting_payload",
    "render_document_drafting_payload_for_output_dir",
    "render_judgment_document_payload",
    # 元典法条/案例检索
    "YUANDIAN_CASE_TOOL_NAME",
    "YUANDIAN_LAW_DETAIL_TOOL_NAME",
    "YUANDIAN_LAW_TOOL_NAME",
    "create_yuandian_case_tool",
    "create_yuandian_law_detail_tool",
    "create_yuandian_law_tool",
    "search_yuandian_case",
    "search_yuandian_law",
    "search_yuandian_law_detail",
    # 刑事 — 起诉书
    "INDICTMENT_DOCUMENT_TYPE",
    "INDICTMENT_TOOL_NAME",
    "INDICTMENT_RESULT_FIELD",
    "INDICTMENT_PDF_FILENAME",
    "IndictmentDraftingTool",
    "create_indictment_drafting_tool",
    # 刑事 — 辩护词
    "DEFENSE_OPINION_DOCUMENT_TYPE",
    "DEFENSE_OPINION_TOOL_NAME",
    "DEFENSE_OPINION_RESULT_FIELD",
    "DEFENSE_OPINION_PDF_FILENAME",
    "DefenseOpinionDraftingTool",
    "create_defense_opinion_drafting_tool",
    # 刑事 — 公诉词
    "PUBLIC_PROSECUTION_DOCUMENT_TYPE",
    "PUBLIC_PROSECUTION_TOOL_NAME",
    "PUBLIC_PROSECUTION_RESULT_FIELD",
    "PUBLIC_PROSECUTION_PDF_FILENAME",
    "PublicProsecutionTool",
    "create_public_prosecution_drafting_tool",
    # 刑事 — 一审判决书
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_DOCUMENT_TYPE",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_TOOL_NAME",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_RESULT_FIELD",
    "CRIMINAL_FIRST_INSTANCE_JUDGMENT_PDF_FILENAME",
    "CriminalFirstInstanceJudgmentDraftingTool",
    "create_first_instance_criminal_judgment_drafting_tool",
    # 刑事 — 二审判决书
    "CRIMINAL_SECOND_INSTANCE_JUDGMENT_DOCUMENT_TYPE",
    "CRIMINAL_SECOND_INSTANCE_JUDGMENT_TOOL_NAME",
    "CRIMINAL_SECOND_INSTANCE_JUDGMENT_RESULT_FIELD",
    "CRIMINAL_SECOND_INSTANCE_JUDGMENT_PDF_FILENAME",
    "CriminalSecondInstanceJudgmentDraftingTool",
    "create_second_instance_criminal_judgment_drafting_tool",
]
