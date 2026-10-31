from ninja import Schema
from typing import List, Optional, Any, List
import uuid
from datetime import date, datetime

class PatientMetadataSchema(Schema):
    extracted_birth_date: Optional[str] = None
    extracted_gender: Optional[str] = None
    extracted_date: Optional[str] = None 

class SummarySchema(Schema):
    is_critical: bool = False
    general_comment: str = ""

class IndicatorSchema(Schema):
    name: str
    slug: Optional[str] = None
    value: Optional[str] = None      # Разрешаем null
    unit: Optional[str] = None       
    ref_range: Optional[str] = None  
    status: Optional[str] = None     # Разрешаем null
    comment: Optional[str] = None
    # Категория для группировки (например, "Печень", "Кроветворение")
    category: Optional[str] = "Общие показатели"

class CauseSchema(Schema):
    title: str
    description: str
    # НОВОЕ ПОЛЕ: Степень серьезности (green, yellow, red)
    severity: Optional[str] = "yellow"

class RecommendationSchema(Schema):
    type: str
    text: str

class AIResultSchema(Schema):
    reasoning: Optional[str] = ""

    patient_info: Optional[PatientMetadataSchema] = None
    summary: Optional[SummarySchema] = None
    indicators: List[IndicatorSchema] = []
    causes: List[CauseSchema] = []
    recommendations: List[RecommendationSchema] = []

class PatientProfileSchema(Schema):
    id: int
    full_name: str
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None

class CreateProfileSchema(Schema):
    full_name: str
    birth_date: Optional[date] = None
    gender: Optional[str] = None 
    weight: Optional[float] = None
    height: Optional[float] = None

class UpdateProfileSchema(Schema):
    full_name: Optional[str] = None
    birth_date: Optional[date] = None
    gender: Optional[str] = None
    weight: Optional[float] = None
    height: Optional[float] = None

class AssignProfileRequest(Schema):
    profile_id: int

class ClaimRequestOTPSchema(Schema):
    # Теперь принимаем список ID
    analysis_uids: List[uuid.UUID]
    email: str
    phone: Optional[str] = None

class ClaimVerifyOTPSchema(Schema):
    # И здесь тоже список ID
    analysis_uids: List[uuid.UUID]
    email: str
    phone: Optional[str] = None
    code: Optional[str] = None
    password: Optional[str] = None

class AuthResponseSchema(Schema):
    token: str
    refresh_token: str
    user_email: str
    is_pro: bool = False
    pro_expires_at: Optional[datetime] = None
    warning: Optional[str] = None
    
class RefreshRequestSchema(Schema):
    refresh: str

class AnalysisResponseSchema(Schema):
    uid: uuid.UUID
    status: str
    created_at: datetime
    ai_result: Optional[AIResultSchema] = None
    patient_profile_id: Optional[int] = None
    parent_analysis_uid: Optional[uuid.UUID] = None 
    
    @staticmethod
    def resolve_patient_profile_id(obj):
        return obj.patient_id
        
    # Резолвер, чтобы отдать на фронт UUID родительского анализа (если он есть)
    @staticmethod
    def resolve_parent_analysis_uid(obj):
        return obj.parent_analysis.uid if obj.parent_analysis else None

class IndicatorHistoryPoint(Schema):
    date: date  # <--- ИСПРАВЛЕНО: было datetime.date, стало просто date
    value: float
    unit: Optional[str] = None
    analysis_uid: uuid.UUID

class ChartResponseSchema(Schema):
    slug: str
    name: str
    data: List[IndicatorHistoryPoint]
