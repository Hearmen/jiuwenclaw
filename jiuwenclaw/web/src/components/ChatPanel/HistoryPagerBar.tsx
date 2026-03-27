import { useTranslation } from 'react-i18next';

export interface HistoryPagerBarProps {
  loadedPages: number;
  totalPages: number;
  loadingMore: boolean;
  onLoadMore: () => void;
}

export function HistoryPagerBar({
  loadedPages,
  totalPages,
  loadingMore,
  onLoadMore,
}: HistoryPagerBarProps) {
  const { t } = useTranslation();
  const hasMore = loadedPages < totalPages;

  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    // 只在有更多内容且未加载中时处理
    if (hasMore && !loadingMore) {
      // 向上滚动触发加载更多
      if (e.deltaY < 0) {
        e.preventDefault();
        void onLoadMore();
      }
    }
  };

  return (
    <div 
      className="history-pager-bar mb-3 rounded-lg border border-white/10 bg-secondary/50 px-3 py-2.5 flex flex-wrap items-center justify-between gap-2 text-sm cursor-pointer"
      onWheel={handleWheel}
      title={t('chat.historyPager.wheelToLoadMore')}
    >
      <span className="text-text-muted tabular-nums">
        {t('chat.historyPager.loadedOfTotal', { loaded: loadedPages, total: totalPages })}
      </span>
      {hasMore ? (
        <span className="text-xs text-text-muted shrink-0">
          {loadingMore ? t('chat.historyPager.loadingMore') : t('chat.historyPager.wheelToLoadMore')}
        </span>
      ) : (
        <span className="text-xs text-text-muted shrink-0">{t('chat.historyPager.allLoaded')}</span>
      )}
    </div>
  );
}
