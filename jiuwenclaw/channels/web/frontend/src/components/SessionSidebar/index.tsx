/**
 * SessionSidebar Component
 *
 * Redesigned sidebar with logo, navigation, and advanced config panel.
 */

import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import './SessionSidebar.css';
import dialogueIcon from '../../assets/sidebar/dialogue.svg';
import agentIcon from '../../assets/sidebar/agent.svg';
import sessionIcon from '../../assets/sidebar/session.svg';
import heartbeatIcon from '../../assets/sidebar/heartbeat.svg';
import cronIcon from '../../assets/sidebar/cron.svg';
import skillIcon from '../../assets/sidebar/skill.svg';
import channelIcon from '../../assets/sidebar/channel.svg';
import pluginIcon from '../../assets/sidebar/plugin.svg';
import configIcon from '../../assets/sidebar/config.svg';
import webIcon from '../../assets/sidebar/web.svg';
import logsIcon from '../../assets/sidebar/logs.svg';
import plusIcon from '../../assets/sidebar/plus.svg';
import logoIcon from '../../assets/sidebar/logo.svg';
import advancedConfigIcon from '../../assets/sidebar/advanced-config-new.svg';
import collapseIcon from '../../assets/sidebar/collapse.svg';
import appearanceSystemIcon from '../../assets/sidebar/appearance-system.svg';
import appearanceDarkIcon from '../../assets/sidebar/appearance-dark.svg';
import appearanceLightIcon from '../../assets/sidebar/appearance-light.svg';

type MainNavKey = 'chat' | 'skills' | 'agents' | 'teams' | 'sessions' | 'heartbeat' | 'cron' | 'channels' | 'extensions' | 'configpanel' | 'logspanel' | 'browserpanel' | 'updatepanel';

interface SessionSidebarProps {
  activeNav: MainNavKey;
  onNavigate: (nav: MainNavKey) => void;
  sessionId: string;
  appVersion: string;
  isConnected: boolean;
  onNewSession?: () => void;
  onCollapse?: () => void;
}

interface NavItem {
  key: MainNavKey;
  labelKey: string;
  icon: React.ReactNode;
}

const mainNavItems: NavItem[] = [
  { key: 'chat', labelKey: 'nav.chat', icon: <img src={dialogueIcon} alt="" /> },
  { key: 'agents', labelKey: 'nav.agent', icon: <img src={agentIcon} alt="" /> },
  { key: 'sessions', labelKey: 'nav.sessions', icon: <img src={sessionIcon} alt="" /> },
  { key: 'heartbeat', labelKey: 'nav.heartbeat', icon: <img src={heartbeatIcon} alt="" /> },
  { key: 'cron', labelKey: 'nav.cron', icon: <img src={cronIcon} alt="" /> },
  { key: 'skills', labelKey: 'nav.skills', icon: <img src={skillIcon} alt="" /> },
  { key: 'channels', labelKey: 'nav.channels', icon: <img src={channelIcon} alt="" /> },
  { key: 'extensions', labelKey: 'nav.extensions', icon: <img src={pluginIcon} alt="" /> },
];

const settingsNavItems: NavItem[] = [
  { key: 'configpanel', labelKey: 'nav.config', icon: <img src={configIcon} alt="" /> },
  { key: 'browserpanel', labelKey: 'nav.browser', icon: <img src={webIcon} alt="" /> },
  { key: 'logspanel', labelKey: 'nav.logs', icon: <img src={logsIcon} alt="" /> },
];

// Advanced Config Panel Component
function AdvancedConfigPanel({
  isOpen,
  onClose,
  isConnected,
  buttonRef,
}: {
  isOpen: boolean;
  onClose: () => void;
  isConnected: boolean;
  buttonRef: React.RefObject<HTMLButtonElement>;
}) {
  const { i18n } = useTranslation();
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'light');
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        panelRef.current &&
        !panelRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        onClose();
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isOpen, onClose, buttonRef]);

  const handleLanguageChange = (lang: 'zh' | 'en') => {
    i18n.changeLanguage(lang);
    void fetch('/api/locale.set_conf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preferred_language: lang }),
    }).catch(() => {});
  };

  const handleThemeChange = (newTheme: string) => {
    setTheme(newTheme);
    localStorage.setItem('theme', newTheme);
    if (newTheme === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  };

  const isZh = i18n.language.startsWith('zh');

  if (!isOpen) return null;

  return (
    <div ref={panelRef} className="advanced-config-panel">
      {/* Connection Status Row */}
      <div className="config-row">
        <span className="config-row__label">连接状态</span>
        <div className={`connection-status ${isConnected ? 'connection-status--connected' : 'connection-status--disconnected'}`}>
          <span className="connection-status__dot" />
          <span className="connection-status__text">
            {isConnected ? '已连接' : '未连接'}
          </span>
        </div>
      </div>

      {/* Language Row */}
      <div className="config-row">
        <span className="config-row__label">语言</span>
        <div className="segmented-control">
          <button
            className={`segmented-control__btn ${isZh ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleLanguageChange('zh')}
          >
            中
          </button>
          <button
            className={`segmented-control__btn ${!isZh ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleLanguageChange('en')}
          >
            En
          </button>
        </div>
      </div>

      {/* Appearance Row */}
      <div className="config-row">
        <span className="config-row__label">外观</span>
        <div className="segmented-control segmented-control--icons">
          <button
            className={`segmented-control__btn ${theme === 'system' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('system')}
            title="跟随系统"
          >
            <img src={appearanceSystemIcon} alt="" />
          </button>
          <button
            className={`segmented-control__btn ${theme === 'dark' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('dark')}
            title="深色模式"
          >
            <img src={appearanceDarkIcon} alt="" />
          </button>
          <button
            className={`segmented-control__btn ${theme === 'light' ? 'segmented-control__btn--active' : ''}`}
            onClick={() => handleThemeChange('light')}
            title="浅色模式"
          >
            <img src={appearanceLightIcon} alt="" />
          </button>
        </div>
      </div>
    </div>
  );
}

export function SessionSidebar({
  activeNav,
  onNavigate,
  sessionId: _sessionId,
  appVersion,
  isConnected,
  onNewSession,
  onCollapse,
}: SessionSidebarProps) {
  const { t } = useTranslation();
  const [advancedConfigOpen, setAdvancedConfigOpen] = useState(false);
  const advancedBtnRef = useRef<HTMLButtonElement>(null);

  const handleNewSession = () => {
    onNavigate('chat');
    if (onNewSession) {
      onNewSession();
    }
  };

  const toggleAdvancedConfig = () => {
    setAdvancedConfigOpen(!advancedConfigOpen);
  };

  const toggleCollapse = () => {
    if (onCollapse) {
      onCollapse();
    }
  };

  return (
    <aside className="sidebar">
      {/* Header Row: Logo + Collapse Button */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <img src={logoIcon} alt="Logo" width="28" height="28" />
        </div>
        <button
          className="collapse-btn"
          title="收起侧边栏"
          onClick={toggleCollapse}
        >
          <img src={collapseIcon} alt="" />
        </button>
      </div>

      {/* 智能体 Section */}
      <div className="nav-section">
        <div className="nav-section-label">智能体</div>
        {/* New Chat Button - inside 智能体 section */}
        <button className="new-chat-btn" onClick={handleNewSession}>
          <span className="new-chat-btn__left">
            <img src={plusIcon} alt="" />
            <span className="new-chat-btn__text">新建会话</span>
          </span>
        </button>
        {/* Navigation items */}
        <nav className="sidebar-nav">
          {mainNavItems.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${activeNav === item.key ? 'active' : ''}`}
              onClick={() => onNavigate(item.key)}
            >
              <span className="nav-item__icon">{item.icon}</span>
              <span className="nav-item__text">{t(item.labelKey)}</span>
            </button>
          ))}
        </nav>
      </div>

      {/* Settings Section */}
      <div className="nav-section">
        <div className="nav-section-label">设置</div>
        <nav className="sidebar-nav">
          {settingsNavItems.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${activeNav === item.key ? 'active' : ''}`}
              onClick={() => onNavigate(item.key)}
            >
              <span className="nav-item__icon">{item.icon}</span>
              <span className="nav-item__text">{t(item.labelKey)}</span>
            </button>
          ))}
        </nav>
      </div>

      {/* User Info Bar - Bottom Row */}
      <div className="sidebar-bottom">
        <div className="sidebar-user">
          <span className="sidebar-user__name">{t('version', { version: appVersion })}</span>
        </div>
        <button
          ref={advancedBtnRef}
          className="advanced-config-btn"
          onClick={toggleAdvancedConfig}
          title="高级配置"
        >
          <img src={advancedConfigIcon} alt="" />
        </button>
      </div>

      {/* Advanced Config Panel - positioned near the button */}
      <AdvancedConfigPanel
        isOpen={advancedConfigOpen}
        onClose={() => setAdvancedConfigOpen(false)}
        isConnected={isConnected}
        buttonRef={advancedBtnRef}
      />
    </aside>
  );
}
