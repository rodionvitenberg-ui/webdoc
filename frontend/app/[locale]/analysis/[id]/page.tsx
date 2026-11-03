'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { getAnalysisResult, viewOriginalFile } from '@/lib/api';
import { AnalysisResponse } from '@/lib/types';
import { ReasoningBlock } from '@/components/analysis/ReasoningBlock';
import { pdf } from '@react-pdf/renderer';
import { AnalysisPDF } from '@/components/analysis/AnalysisPDF';
import { FileUploader } from '@/components/home/FileUploader';
import { X } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { AnalysisTreeSidebar } from '@/components/analysis/AnalysisTreeSidebar';
import StaticBackground from '@/components/background/StaticBackground';
import { RefineAnalysisBlock } from '@/components/analysis/RefineAnalysisBlock';
import { AnalysisChat } from '@/components/analysis/AnalysisChat';
import { downloadBlob } from '@/lib/analysis-utils';
import { API_BASE_URL } from '@/lib/api';

// Импорты наших новых модулей
import { AnalysisLoading } from '@/components/analysis/AnalysisLoading';
import { AnalysisFailed } from '@/components/analysis/AnalysisFailed';
import { AnalysisHeader } from '@/components/analysis/AnalysisHeader';
import { IndicatorsTable } from '@/components/analysis/IndicatorsTable';
import { CausesAndRecommendations } from '@/components/analysis/CausesAndRecommendations';

export default function AnalysisPage() {
  const params = useParams();
  const id = params.id as string;
  const t = useTranslations('Analysis.Page');
  
  const [data, setData] = useState<AnalysisResponse | null>(null);
  const [isPolling, setIsPolling] = useState(true);
  const [isGeneratingPDF, setIsGeneratingPDF] = useState(false);
  const [isViewingOriginal, setIsViewingOriginal] = useState(false);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);

  // Обновленный useEffect с рекурсивным setTimeout для защиты от DDOS
  useEffect(() => {
    let eventSource: EventSource | null = null;
    let isMounted = true;

    const loadInitialData = async () => {
      try {
        // Сначала забираем текущие данные
        const res = await getAnalysisResult(id);
        if (!isMounted) return;
        
        setData(res);
        
        // Если анализ уже готов или упал — останавливаем ожидание
        if (res.status === 'completed' || res.status === 'failed') {
          setIsPolling(false);
        } else {
          // Если он всё еще в обработке, открываем потоковое соединение (SSE)
          eventSource = new EventSource(`${API_BASE_URL}/analyses/${id}/status-stream`);

          eventSource.onmessage = async (event) => {
            const status = event.data;
            
            // Если сервер сообщил, что всё готово
            if (status === 'completed' || status === 'failed') {
              if (eventSource) eventSource.close(); // Закрываем поток
              setIsPolling(false);
              
              // Делаем ОДИН финальный запрос, чтобы скачать готовый JSON с результатами
              try {
                const finalRes = await getAnalysisResult(id);
                if (isMounted) setData(finalRes);
              } catch (e) {
                console.error("Error fetching final data:", e);
              }
            } else {
              // Если статус изменился (например, с pending на processing), обновляем UI
              setData(prev => prev ? { ...prev, status: status as any } : null);
            }
          };

          eventSource.onerror = () => {
            // Если соединение оборвалось (например, мигнул интернет)
            if (eventSource) eventSource.close();
          };
        }
      } catch (err) {
        console.error("Error loading initial data:", err);
      }
    };

    loadInitialData();

    // Cleanup: закрываем соединение, если юзер ушел со страницы до завершения анализа
    return () => {
      isMounted = false;
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [id]);

  const handleDownloadPDF = async () => {
    if (!data) return;
    setIsGeneratingPDF(true);
    try {
      const blob = await pdf(<AnalysisPDF data={data} />).toBlob();
      downloadBlob(blob, `Checkups_Report_${id.slice(0, 8)}.pdf`);
    } catch (error) {
      console.error("Ошибка при генерации PDF:", error);
      alert(t('pdfError'));
    } finally {
      setIsGeneratingPDF(false);
    }
  };

  const handleViewOriginal = async () => {
    const newWindow = window.open('', '_blank');
    setIsViewingOriginal(true);
    try {
      const fileUrl = await viewOriginalFile(id); 
      if (newWindow) {
        newWindow.location.href = fileUrl; 
      } else {
        window.location.href = fileUrl; 
      }
      setTimeout(() => URL.revokeObjectURL(fileUrl), 10000);
    } catch (error) {
      console.error("Ошибка открытия оригинала:", error);
      if (newWindow) newWindow.close(); 
      alert(t('originalError'));
    } finally {
      setIsViewingOriginal(false);
    }
  };

  // Упавший анализ показываем отдельным экраном, а не вечной загрузкой
  if (data && data.status === 'failed') {
    return (
      <main className="relative min-h-screen flex flex-col items-center justify-center pt-20 px-4">
        <StaticBackground imageUrl="/background/analisis.png" />
        <AnalysisFailed />
      </main>
    );
  }

  if (!data || data.status !== 'completed') {
    return (
      <main className="relative min-h-screen flex flex-col items-center justify-center pt-20 px-4">
        <StaticBackground imageUrl="/background/analisis.png" />
        <AnalysisLoading isPolling={isPolling} />
      </main>
    );
  }

  const result = data.ai_result!;

  return (
    <main className="relative min-h-screen pt-28 pb-16 px-4 sm:px-8 md:pt-36 md:pb-24 font-sans animate-in fade-in duration-700">
      <StaticBackground imageUrl="/background/analisis.png" />

      <div className="relative z-10 max-w-7xl mx-auto flex flex-col lg:flex-row gap-6 lg:gap-8 items-start">
        
        {/* ЛЕВАЯ КОЛОНКА (Сайдбар) */}
        <div className="w-full lg:w-[320px] shrink-0 order-2 lg:order-1 lg:sticky lg:top-36">
            <AnalysisTreeSidebar currentId={id} />
            
            {/* НОВЫЙ БЛОК РЕ-АНАЛИЗА */}
            <RefineAnalysisBlock analysis={data} />
            <AnalysisChat analysisUid={id} />
        </div>
        

        {/* ПРАВАЯ КОЛОНКА (Контент) */}
        <div className="flex-1 w-full space-y-6 order-1 lg:order-2">
            
            <AnalysisHeader 
                data={data}
                isViewingOriginal={isViewingOriginal}
                isGeneratingPDF={isGeneratingPDF}
                onAddAnalysis={() => setIsUploadModalOpen(true)}
                onViewOriginal={handleViewOriginal}
                onDownloadPDF={handleDownloadPDF}
            />

            {result.reasoning && (
                <ReasoningBlock text={result.reasoning} />
            )}

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
                <IndicatorsTable indicators={result.indicators} />
                <CausesAndRecommendations causes={result.causes} recommendations={result.recommendations} />
            </div>

            {/* ДИСКЛЕЙМЕР */}
            <div className="bg-white/40 backdrop-blur-md rounded-xl shadow-sm border border-white/50 p-6 text-center mt-8">
                <p className="text-sm text-slate-600 leading-relaxed font-medium">
                    <span className="font-bold text-slate-900">{t('disclaimerTitle')}</span> {t('disclaimerText')}
                </p>
            </div>

        </div>

        {/* МОДАЛЬНОЕ ОКНО ЗАГРУЗКИ */}
        {isUploadModalOpen && (
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60 backdrop-blur-sm transition-opacity">
                <div className="bg-white/95 backdrop-blur-xl rounded-3xl shadow-2xl w-full max-w-xl overflow-hidden animate-in fade-in zoom-in-95 duration-200 relative border border-white/20">
                    <div className="flex justify-between items-center p-6 border-b border-slate-100 bg-slate-50/50">
                        <h3 className="text-xl font-bold text-slate-900">{t('uploadModalTitle')}</h3>
                        <button 
                            onClick={() => setIsUploadModalOpen(false)} 
                            className="text-slate-400 hover:text-slate-600 transition-colors bg-white rounded-full p-1.5 shadow-sm border border-slate-200"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                    <div className="p-6">
                        <FileUploader /> 
                    </div>
                </div>
            </div>
        )}
      </div>
    </main>
  );
}