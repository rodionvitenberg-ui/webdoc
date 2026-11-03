'use client';

import { XCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { Link } from '@/i18n/routing';

export function AnalysisFailed() {
    const t = useTranslations('Analysis.Failed');

    return (
        <div className="relative z-10 bg-white/80 backdrop-blur-md border border-white/40 rounded-3xl shadow-xl shadow-[#3f94ca]/10 p-8 sm:p-12 flex flex-col items-center max-w-md w-full animate-in zoom-in-95 duration-500">
            <XCircle className="w-16 h-16 text-red-400 mb-6" />
            <h3 className="text-lg font-bold text-slate-900 text-center mb-2">{t('title')}</h3>
            <p className="text-slate-500 text-center font-medium mb-8">{t('text')}</p>
            <Link
                href="/"
                className="px-6 py-3 rounded-xl bg-[#00be64] hover:bg-[#00a957] text-white font-semibold transition-colors"
            >
                {t('cta')}
            </Link>
        </div>
    );
}