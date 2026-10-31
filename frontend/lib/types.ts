// lib/types.ts

export interface AuthResponse {
    token: string;
    refresh_token: string; 
    user_email: string;
    is_pro: boolean;
    pro_expires_at?: string | null;
    warning?: string | null;
}

export interface PatientMetadata {
    extracted_name?: string;
    extracted_birth_date?: string;
    extracted_gender?: string;
    extracted_date?: string;
}

export interface AIIndicator {
    name: string;
    slug?: string;
    value: string;
    unit?: string;
    ref_range?: string;
    status: 'normal' | 'low' | 'high' | 'critical';
    comment?: string;
    category?: string;
}

export interface AICause {
    title: string;
    description: string;
    severity?: 'green' | 'yellow' | 'red';
}

export interface AIRecommendation {
    type: string;
    text: string;
}

export interface AISummary {
    is_critical: boolean;
    general_comment: string;
}

export interface AIResult {
    reasoning?: string;
    patient_info?: PatientMetadata;
    summary?: AISummary;
    indicators: AIIndicator[];
    causes: AICause[];
    recommendations: AIRecommendation[];
}

export interface AnalysisResponse {
    uid: string;
    status: 'pending' | 'processing' | 'completed' | 'failed';
    created_at: string;
    ai_result?: AIResult;
    patient_profile_id?: number;
}

export interface PatientProfile {
    id: number;
    full_name: string;
    birth_date?: string | null;
    gender?: 'M' | 'F' | null;
    weight?: number | null; // Вес в кг
    height?: number | null; // Рост в см
}

export interface ChartPoint {
    date: string;
    value: number;
    unit?: string;
    analysis_uid: string;
}

export interface ChartData {
    slug: string;
    name: string;
    data: ChartPoint[];
}

export interface Trait {
    id: number;
    name: string;
    category: 'disease' | 'bad_habit' | 'good_habit' | 'feature';
    is_custom: boolean;
}

export interface PatientTraitLink {
    id: number;
    trait: Trait;
    details: string | null;
    created_at: string;
}