import { Link } from '@/i18n/routing';
import Image from "next/image";
import { useTranslations } from 'next-intl';

export function Footer() {
  const t = useTranslations('Footer');
  const currentYear = new Date().getFullYear();

  return (
    <footer className="w-full backdrop-blur-md mt-auto border-t border-accent/10">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-8 md:py-12">
        <div className="flex flex-col md:flex-row justify-between items-center gap-8">
          
          {/* Левая часть: Логотип и копирайт */}
          <div className="flex flex-col items-center md:items-start gap-4">
            <Link href="/" className="hover:opacity-80 transition-opacity">
              <Image 
                src="/logo.png" 
                alt={t('logoAlt')} 
                width={140} 
                height={40} 
                className="h-10 w-auto object-contain" 
                unoptimized
              />
            </Link>
            <p className="text-sm text-accent/60 font-medium">
              {t('copyright', { year: currentYear })}
            </p>
          </div>

          {/* Правая часть: Ссылки в две колонки */}
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm font-bold text-secondary tracking-wide uppercase">
            <div className="flex flex-col gap-3">
              <Link href="/legal" className="hover:text-accent transition-colors">
                {t('privacy')}
              </Link>
              <Link href="/terms" className="hover:text-accent transition-colors">
                {t('terms')}
              </Link>
            </div>
            <div className="flex flex-col gap-3">
              <Link href="/cookies" className="hover:text-accent transition-colors">
                {t('cookies')}
              </Link>
              <Link href="/disclaimer" className="hover:text-accent transition-colors">
                {t('disclaimer')}
              </Link>
            </div>
          </div>

        </div>
      </div>
    </footer>
  );
}