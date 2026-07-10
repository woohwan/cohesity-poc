"""
소스(=토픽) 디렉터리명 -> 사람이 읽기 편한 표시용 라벨.

주의: 이 파일은 표시/리포트 전용이다. config.yaml의 소스 키, 실제 수집
디렉터리명(gaia_test_200g_kr80_no_ocr/*), manifest.csv의 source/path 컬럼은
전혀 건드리지 않는다 — 그쪽 이름을 바꾸려면 실행 중인 수집 프로세스를 멈추고
gaia_collect.py에 하드코딩된 소스명(kowiki_knowledge/common_crawl_ko_text/
law_lbook/namuwiki_dump 등)까지 같이 고쳐야 해서 별도로 다루지 않기로 함
(2026-07-10 결정).

매핑에 없는 소스는 원래 이름을 그대로 반환한다 (fallback) — 이미 이름 자체로
충분히 명확한 소스(dart_financial, kosis_statistics, law_open_data 등)는
일부러 넣지 않았다. 기관 약어 추정이 틀렸으면 여기 값만 고치면 된다.
"""

TOPIC_LABELS = {
    "alio_excel":              "public_agency_finance",   # ALIO 공공기관 경영정보
    "bok_eng":                 "bank_of_korea_eng",
    "bok_publications":        "bank_of_korea",
    "customs_excel":           "customs_service",
    "gov_policy_reports":      "gov_policy",
    "hira_opendata":           "health_insurance_review",  # HIRA
    "investkorea_eng":         "invest_korea_eng",
    "kdca_health":             "disease_control_agency",   # KDCA
    "kdi_edu_ppt":             "kdi_edu_slides",
    "kdi_eng_reports":         "kdi_reports_eng",
    "kdi_reports":             "kdi_reports",              # 한국개발연구원
    "kdischool_eng":           "kdi_school_eng",
    "keei_seminar":            "energy_economics_institute",  # KEEI
    "kei_reports":             "environment_institute",   # KEI
    "khidi_reports":           "health_industry_institute",  # KHIDI
    "kistep_reports":          "sci_tech_policy_institute",   # KISTEP
    "kisti_reports":           "sci_tech_info_institute",  # KISTI
    "knto_tourism":            "tourism_organization",     # KNTO
    "koroad_stats":            "road_traffic_authority",   # KoROAD
    "kosha_guidelines":        "safety_health_agency",     # KOSHA
    "kostat_edu":              "statistics_korea_edu",
    "kostat_eng":              "statistics_korea_eng",
    "koti_seminar":            "transport_institute",      # KOTI
    "kowiki_knowledge":        "korean_wikipedia",
    "kpta_hwp":                "patent_translators_assoc", # KPTA (추정)
    "krei_reports":            "rural_economy_institute",  # KREI
    "krihs_reports":           "land_urban_institute",     # KRIHS
    "law_lbook":               "law",
    "mafra_food":              "agriculture_food_ministry",   # MAFRA
    "mcst_culture":            "culture_tourism_ministry",    # MCST
    "me_guidelines":           "environment_ministry",        # ME
    "mfds_hwp":                "food_drug_safety_ministry",   # MFDS
    "mist_plans":              "ict_ministry_plans",          # MSIT(추정)
    "mlit_plans":              "land_transport_plans",        # 추정
    "moe_publications":        "education_ministry",          # MOE
    "moef_briefing":           "finance_ministry_briefing",   # MOEF
    "moef_budget":             "finance_ministry_budget",
    "moef_eng":                "finance_ministry_eng",
    "moef_hwp":                "finance_ministry_hwp",
    "moel_stats":              "labor_ministry_stats",        # MOEL
    "mof_docs":                "oceans_fisheries_ministry",   # MOF
    "mogef_family":            "gender_family_ministry",      # MOGEF
    "mohw_guidelines":         "health_welfare_ministry",      # MOHW
    "mois_data":               "interior_safety_ministry",     # MOIS
    "motie_industry":          "industry_trade_ministry",       # MOTIE
    "mss_eng":                 "smes_startups_ministry_eng",   # MSS(추정)
    "national_assembly_reports": "national_assembly",
    "nhis_stats":              "health_insurance_service",     # NHIS
    "nia_reports":             "intelligence_society_agency",  # NIA
    "nia_seminar":             "intelligence_society_agency_seminar",
    "nipa_reports":            "ict_industry_promotion",       # NIPA
    "nrf_reports":             "research_foundation",          # NRF
    "nts_hwp":                 "tax_service_hwp",               # NTS
    "nts_stats":               "tax_service_stats",
    "openfiscal_excel":        "open_fiscal_data",
    "prism_policy":            "policy_research_prism",         # PRISM
    "smba_hwp":                "smes_startups_hwp",             # SMBA(구 명칭, 추정)
    "spo_data":                "prosecution_service",           # SPO(추정)
    "unikorea_unification":    "unification_ministry",
}


def topic_label(source: str) -> str:
    """표시용 라벨. 매핑에 없으면 원래 소스명을 그대로 반환한다."""
    return TOPIC_LABELS.get(source, source)
