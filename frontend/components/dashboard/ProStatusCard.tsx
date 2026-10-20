import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Crown, Loader2 } from 'lucide-react';
import { getPremiumStatus, createPayment } from '@/lib/api';

export function ProStatusCard() {
    const t = useTranslations('Dashboard.ProStatus');
    const [status, setStatus] = useState<{ is_pro: boolean; pro_expires_at: string | null } | null>(null);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        getPremiumStatus()
            .then(setStatus)
            .catch(() => setStatus({ is_pro: false, pro_expires_at: null }));
    }, []);

    const handleBuy = async () => {
        setIsLoading(true);
        setError(null);
        try {
            const data = await createPayment();
            if (data.payment_url) {
                window.location.href = data.payment_url;
            } else {
                setIsLoading(false);
            }
        } catch (error) {
            console.error("Payment error:", error);
            setError(t('paymentError'));
            setIsLoading(false);
        }
    };

    if (!status) return null;

    const isActive = status.is_pro && !!status.pro_expires_at;
    const formattedDate = status.pro_expires_at
        ? new Date(status.pro_expires_at).toLocaleDateString()
        : '';

    return (
        <div className="bg-gradient-to-br from-amber-50 to-orange-50/50 backdrop-blur-md p-5 sm:p-7 rounded-3xl border border-amber-200/50 shadow-sm relative overflow-hidden mb-8">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                    <div className="w-12 h-12 bg-gradient-to-br from-amber-400 to-orange-500 rounded-2xl flex items-center justify-center shadow-lg shadow-orange-500/20 shrink-0">
                        <Crown className="w-6 h-6 text-white" />
                    </div>
                    <div>
                        <h3 className="font-bold text-slate-900 text-lg">
                            {isActive ? t('activeTitle') : t('inactiveTitle')}
                        </h3>
                        <p className="text-sm text-slate-500">
                            {isActive
                                ? t('activeUntil', { date: formattedDate })
                                : t('inactiveDesc')}
                        </p>
                    </div>
                </div>

                {!isActive && (
                    <button
                        onClick={handleBuy}
                        disabled={isLoading}
                        className="flex items-center justify-center gap-2 px-6 py-3 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white font-bold rounded-xl transition-all shadow-lg shadow-orange-500/20 hover:shadow-orange-500/30 disabled:opacity-70 disabled:cursor-not-allowed"
                    >
                        {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Crown className="w-4 h-4" />}
                        {t('upgradeBtn')}
                    </button>
                )}
            </div>

            {error && (
                <p className="mt-4 text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
                    {error}
                </p>
            )}
        </div>
    );
}