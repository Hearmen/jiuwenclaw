import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import './ChannelsPanel.css';

interface ChannelsPanelProps {
  isConnected: boolean;
}

type ChannelItem = {
  channel_id: SupportedChannelId;
  logo_src: string | null;
  enabled: boolean;
};

type LoadState = 'idle' | 'loading' | 'success' | 'error';
type SupportedChannelId = 'web' | 'xiaoyi' | 'feishu' | 'dingtalk' | 'telegram' | 'discord';
const ADAPTING_CHANNEL_IDS = new Set<SupportedChannelId>([]);

type FeishuConfig = {
  enabled: boolean;
  app_id: string;
  app_secret: string;
  encrypt_key: string;
  verification_token: string;
  chat_id: string;
  allow_from: string[];
};

type FeishuDraft = {
  enabled: boolean;
  app_id: string;
  app_secret: string;
  encrypt_key: string;
  verification_token: string;
  chat_id: string;
  allow_from: string;
};

type XiaoyiConfig = {
  enabled: boolean;
  ak: string;
  sk: string;
  agent_id: string;
  enable_streaming: boolean;
};

type XiaoyiDraft = {
  enabled: boolean;
  ak: string;
  sk: string;
  agent_id: string;
  enable_streaming: boolean;
};

type DingTalkConfig = {
  enabled: boolean;
  client_id: string;
  client_secret: string;
  allow_from: string[];
};

type DingTalkDraft = {
  enabled: boolean;
  client_id: string;
  client_secret: string;
  allow_from: string;
};

type TelegramConfig = {
  enabled: boolean;
  bot_token: string;
  allow_from: string[];
  parse_mode: string;
  group_chat_mode: string;
};

type TelegramDraft = {
  enabled: boolean;
  bot_token: string;
  allow_from: string;
  parse_mode: string;
  group_chat_mode: string;
};

type DiscordConfig = {
  enabled: boolean;
  bot_token: string;
  application_id: string;
  guild_id: string;
  channel_id: string;
  allow_from: string[];
};

type DiscordDraft = {
  enabled: boolean;
  bot_token: string;
  application_id: string;
  guild_id: string;
  channel_id: string;
  allow_from: string;
};

const DEFAULT_FEISHU_CONF: FeishuConfig = {
  enabled: false,
  app_id: '',
  app_secret: '',
  encrypt_key: '',
  verification_token: '',
  chat_id: '',
  allow_from: [],
};

const DEFAULT_XIAOYI_CONF: XiaoyiConfig = {
  enabled: false,
  ak: '',
  sk: '',
  agent_id: '',
  enable_streaming: true,
};

const DEFAULT_DINGTALK_CONF: DingTalkConfig = {
  enabled: false,
  client_id: '',
  client_secret: '',
  allow_from: [],
};

const DEFAULT_TELEGRAM_CONF: TelegramConfig = {
  enabled: false,
  bot_token: '',
  allow_from: [],
  parse_mode: 'Markdown',
  group_chat_mode: 'mention',
};

const DEFAULT_DISCORD_CONF: DiscordConfig = {
  enabled: false,
  bot_token: '',
  application_id: '',
  guild_id: '',
  channel_id: '',
  allow_from: [],
};

const SUPPORTED_CHANNELS: Array<{ channel_id: SupportedChannelId; logo_src: string | null }> = [
  { channel_id: 'web', logo_src: null },
  { channel_id: 'xiaoyi', logo_src: '/xiaoyi.webp' },
  { channel_id: 'feishu', logo_src: '/feishu.webp' },
  { channel_id: 'dingtalk', logo_src: '/dingtalk.png' },
  { channel_id: 'telegram', logo_src: '/telegram.webp' },
  { channel_id: 'discord', logo_src: '/discord.jpg' },
];


function formatTime(iso: string | null, locale: string): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString(locale, { hour12: false });
}

function isSensitiveField(field: keyof FeishuDraft): boolean {
  return field === 'app_secret' || field === 'encrypt_key' || field === 'verification_token';
}

function isSensitiveXiaoyiField(field: keyof XiaoyiDraft): boolean {
  return field === 'ak' || field === 'sk';
}

function isSensitiveDingtalkField(field: keyof DingTalkDraft): boolean {
  return field === 'client_secret';
}

function normalizeEnabledChannels(channels: unknown): Set<string> {
  if (!Array.isArray(channels)) {
    return new Set();
  }
  return new Set(
    channels
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null;
      }
      const channelId = (item as { channel_id?: unknown }).channel_id;
      if (typeof channelId !== 'string' || !channelId.trim()) {
        return null;
      }
      return channelId.trim().toLowerCase();
    })
      .filter((item): item is string => item !== null),
  );
}

function buildChannels(channels: unknown): ChannelItem[] {
  const enabledChannels = normalizeEnabledChannels(channels);
  return SUPPORTED_CHANNELS.map((channel) => ({
    ...channel,
    enabled: enabledChannels.has(channel.channel_id),
  }));
}

function getChannelLabel(t: (key: string) => string, channelId: SupportedChannelId): string {
  return t(`channels.labels.${channelId}`);
}

function normalizeFeishuConfig(input: unknown): FeishuConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_FEISHU_CONF;
  }
  const data = input as Record<string, unknown>;
  const allowFromRaw = Array.isArray(data.allow_from) ? data.allow_from : [];
  const allowFrom = allowFromRaw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0);
  return {
    enabled: Boolean(data.enabled),
    app_id: String(data.app_id ?? '').trim(),
    app_secret: String(data.app_secret ?? '').trim(),
    encrypt_key: String(data.encrypt_key ?? '').trim(),
    verification_token: String(data.verification_token ?? '').trim(),
    chat_id: String(data.chat_id ?? '').trim(),
    allow_from: allowFrom,
  };
}

function draftFromFeishuConfig(conf: FeishuConfig): FeishuDraft {
  return {
    enabled: conf.enabled,
    app_id: conf.app_id,
    app_secret: conf.app_secret,
    encrypt_key: conf.encrypt_key,
    verification_token: conf.verification_token,
    chat_id: conf.chat_id,
    allow_from: conf.allow_from.join('\n'),
  };
}

function normalizeAllowFromText(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function buildFeishuPayload(draft: FeishuDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    app_id: draft.app_id.trim(),
    app_secret: draft.app_secret.trim(),
    encrypt_key: draft.encrypt_key.trim(),
    verification_token: draft.verification_token.trim(),
    chat_id: draft.chat_id.trim(),
    allow_from: normalizeAllowFromText(draft.allow_from),
  };
}

function normalizeXiaoyiConfig(input: unknown): XiaoyiConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_XIAOYI_CONF;
  }
  const data = input as Record<string, unknown>;
  return {
    enabled: Boolean(data.enabled),
    ak: String(data.ak ?? '').trim(),
    sk: String(data.sk ?? '').trim(),
    agent_id: String(data.agent_id ?? '').trim(),
    enable_streaming: data.enable_streaming === undefined ? true : Boolean(data.enable_streaming),
  };
}

function draftFromXiaoyiConfig(conf: XiaoyiConfig): XiaoyiDraft {
  return {
    enabled: conf.enabled,
    ak: conf.ak,
    sk: conf.sk,
    agent_id: conf.agent_id,
    enable_streaming: conf.enable_streaming,
  };
}

function buildXiaoyiPayload(draft: XiaoyiDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    ak: draft.ak.trim(),
    sk: draft.sk.trim(),
    agent_id: draft.agent_id.trim(),
    enable_streaming: draft.enable_streaming,
  };
}

function normalizeDingtalkConfig(input: unknown): DingTalkConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_DINGTALK_CONF;
  }
  const data = input as Record<string, unknown>;
  const allowFromRaw = Array.isArray(data.allow_from) ? data.allow_from : [];
  const allowFrom = allowFromRaw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0);
  return {
    enabled: Boolean(data.enabled),
    client_id: String(data.client_id ?? '').trim(),
    client_secret: String(data.client_secret ?? '').trim(),
    allow_from: allowFrom,
  };
}

function draftFromDingtalkConfig(conf: DingTalkConfig): DingTalkDraft {
  return {
    enabled: conf.enabled,
    client_id: conf.client_id,
    client_secret: conf.client_secret,
    allow_from: conf.allow_from.join('\n'),
  };
}

function buildDingtalkPayload(draft: DingTalkDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    client_id: draft.client_id.trim(),
    client_secret: draft.client_secret.trim(),
    allow_from: normalizeAllowFromText(draft.allow_from),
  };
}

function normalizeTelegramConfig(input: unknown): TelegramConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_TELEGRAM_CONF;
  }
  const data = input as Record<string, unknown>;
  const allowFromRaw = Array.isArray(data.allow_from) ? data.allow_from : [];
  const allowFrom = allowFromRaw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0);
  return {
    enabled: Boolean(data.enabled),
    bot_token: String(data.bot_token ?? '').trim(),
    allow_from: allowFrom,
    parse_mode: String(data.parse_mode ?? 'Markdown').trim(),
    group_chat_mode: String(data.group_chat_mode ?? 'mention').trim(),
  };
}

function draftFromTelegramConfig(conf: TelegramConfig): TelegramDraft {
  return {
    enabled: conf.enabled,
    bot_token: conf.bot_token,
    allow_from: conf.allow_from.join('\n'),
    parse_mode: conf.parse_mode,
    group_chat_mode: conf.group_chat_mode,
  };
}

function buildTelegramPayload(draft: TelegramDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    bot_token: draft.bot_token.trim(),
    allow_from: normalizeAllowFromText(draft.allow_from),
    parse_mode: draft.parse_mode.trim(),
    group_chat_mode: draft.group_chat_mode.trim(),
  };
}

function isSensitiveDiscordField(field: keyof DiscordDraft): boolean {
  return field === 'bot_token';
}

function normalizeDiscordConfig(input: unknown): DiscordConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_DISCORD_CONF;
  }
  const data = input as Record<string, unknown>;
  const allowFromRaw = Array.isArray(data.allow_from) ? data.allow_from : [];
  const allowFrom = allowFromRaw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0);
  return {
    enabled: Boolean(data.enabled),
    bot_token: String(data.bot_token ?? '').trim(),
    application_id: String(data.application_id ?? '').trim(),
    guild_id: String(data.guild_id ?? '').trim(),
    channel_id: String(data.channel_id ?? '').trim(),
    allow_from: allowFrom,
  };
}

function draftFromDiscordConfig(conf: DiscordConfig): DiscordDraft {
  return {
    enabled: conf.enabled,
    bot_token: conf.bot_token,
    application_id: conf.application_id,
    guild_id: conf.guild_id,
    channel_id: conf.channel_id,
    allow_from: conf.allow_from.join('\n'),
  };
}

function buildDiscordPayload(draft: DiscordDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    bot_token: draft.bot_token.trim(),
    application_id: draft.application_id.trim(),
    guild_id: draft.guild_id.trim(),
    channel_id: draft.channel_id.trim(),
    allow_from: normalizeAllowFromText(draft.allow_from),
  };
}

function VisibilityIcon({ visible }: { visible: boolean }) {
  return visible ? (
    <svg className="channels-panel__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.58 10.58A2 2 0 0013.42 13.42" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.88 5.09A10.94 10.94 0 0112 4.9c5.05 0 9.27 3.11 10.5 7.5a11.6 11.6 0 01-3.06 4.88" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M6.61 6.61A11.6 11.6 0 001.5 12.4c.53 1.9 1.63 3.56 3.11 4.79" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.12 14.12a3 3 0 01-4.24-4.24" />
    </svg>
  ) : (
    <svg className="channels-panel__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M1.5 12s3.75-7.5 10.5-7.5S22.5 12 22.5 12s-3.75 7.5-10.5 7.5S1.5 12 1.5 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ChannelLogo({ channel, label }: { channel: ChannelItem; label: string }) {
  if (channel.logo_src) {
    return (
      <img
        src={channel.logo_src}
        alt={`${label} logo`}
        className="h-6 w-6 rounded-md border border-border object-contain bg-card"
      />
    );
  }
  return (
    <span className="h-6 w-6 rounded-md border border-border bg-card flex items-center justify-center text-text-muted">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
        <circle cx="12" cy="12" r="9" />
        <path strokeLinecap="round" d="M3 12h18M12 3c2.5 2.2 4 5.5 4 9s-1.5 6.8-4 9m0-18c-2.5 2.2-4 5.5-4 9s1.5 6.8 4 9" />
      </svg>
    </span>
  );
}

function ChannelHeaderLogo({ channelId, label }: { channelId: SupportedChannelId; label: string }) {
  const logoSrc = SUPPORTED_CHANNELS.find((channel) => channel.channel_id === channelId)?.logo_src ?? null;
  if (logoSrc) {
    return (
      <img
        src={logoSrc}
        alt={`${label} logo`}
        className="h-9 w-9 rounded-lg border border-border object-contain bg-card"
      />
    );
  }
  return (
    <span className="h-9 w-9 rounded-lg border border-border bg-card flex items-center justify-center text-text-muted">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-7 w-7">
        <circle cx="12" cy="12" r="9" />
        <path strokeLinecap="round" d="M3 12h18M12 3c2.5 2.2 4 5.5 4 9s-1.5 6.8-4 9m0-18c-2.5 2.2-4 5.5-4 9s1.5 6.8 4 9" />
      </svg>
    </span>
  );
}

export function ChannelsPanel({ isConnected }: ChannelsPanelProps) {
  const { t, i18n } = useTranslation();
  const [channels, setChannels] = useState<ChannelItem[]>(() => buildChannels([]));
  const [activeChannelId, setActiveChannelId] = useState<SupportedChannelId>('xiaoyi');
  const [loadState, setLoadState] = useState<LoadState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  const [feishuConfig, setFeishuConfig] = useState<FeishuConfig>(DEFAULT_FEISHU_CONF);
  const [draft, setDraft] = useState<FeishuDraft>(draftFromFeishuConfig(DEFAULT_FEISHU_CONF));
  const [visibleFields, setVisibleFields] = useState<Record<string, boolean>>({});
  const [feishuLoading, setFeishuLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [xiaoyiConfig, setXiaoyiConfig] = useState<XiaoyiConfig>(DEFAULT_XIAOYI_CONF);
  const [xiaoyiDraft, setXiaoyiDraft] = useState<XiaoyiDraft>(draftFromXiaoyiConfig(DEFAULT_XIAOYI_CONF));
  const [xiaoyiVisibleFields, setXiaoyiVisibleFields] = useState<Record<string, boolean>>({});
  const [xiaoyiLoading, setXiaoyiLoading] = useState(false);
  const [xiaoyiSaving, setXiaoyiSaving] = useState(false);
  const [xiaoyiSaveError, setXiaoyiSaveError] = useState<string | null>(null);
  const [xiaoyiSuccess, setXiaoyiSuccess] = useState<string | null>(null);
  const [dingtalkConfig, setDingtalkConfig] = useState<DingTalkConfig>(DEFAULT_DINGTALK_CONF);
  const [dingtalkDraft, setDingtalkDraft] = useState<DingTalkDraft>(draftFromDingtalkConfig(DEFAULT_DINGTALK_CONF));
  const [dingtalkVisibleFields, setDingtalkVisibleFields] = useState<Record<string, boolean>>({});
  const [dingtalkLoading, setDingtalkLoading] = useState(false);
  const [dingtalkSaving, setDingtalkSaving] = useState(false);
  const [dingtalkSaveError, setDingtalkSaveError] = useState<string | null>(null);
  const [dingtalkSuccess, setDingtalkSuccess] = useState<string | null>(null);
  const [telegramConfig, setTelegramConfig] = useState<TelegramConfig>(DEFAULT_TELEGRAM_CONF);
  const [telegramDraft, setTelegramDraft] = useState<TelegramDraft>(draftFromTelegramConfig(DEFAULT_TELEGRAM_CONF));
  const [telegramVisibleFields, setTelegramVisibleFields] = useState<Record<string, boolean>>({});
  const [telegramLoading, setTelegramLoading] = useState(false);
  const [telegramSaving, setTelegramSaving] = useState(false);
  const [telegramSaveError, setTelegramSaveError] = useState<string | null>(null);
  const [telegramSuccess, setTelegramSuccess] = useState<string | null>(null);
  const [discordConfig, setDiscordConfig] = useState<DiscordConfig>(DEFAULT_DISCORD_CONF);
  const [discordDraft, setDiscordDraft] = useState<DiscordDraft>(draftFromDiscordConfig(DEFAULT_DISCORD_CONF));
  const [discordVisibleFields, setDiscordVisibleFields] = useState<Record<string, boolean>>({});
  const [discordLoading, setDiscordLoading] = useState(false);
  const [discordSaving, setDiscordSaving] = useState(false);
  const [discordSaveError, setDiscordSaveError] = useState<string | null>(null);
  const [discordSuccess, setDiscordSuccess] = useState<string | null>(null);

  const fetchChannels = useCallback(async () => {
    setLoadState('loading');
    setError(null);
    try {
      const payload = await webRequest<{ channels?: unknown[] }>('channel.get');
      setChannels(buildChannels(payload?.channels));
      setLoadState('success');
      setLastUpdatedAt(new Date().toISOString());
    } catch (err) {
      setChannels(buildChannels([]));
      setLoadState('error');
      setError(err instanceof Error ? err.message : t('channels.errors.loadChannels'));
    }
  }, [t]);

  useEffect(() => {
    void fetchChannels();
  }, [fetchChannels]);

  const fetchFeishuConfig = useCallback(async () => {
    setFeishuLoading(true);
    setSaveError(null);
    setSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.feishu.get_conf');
      const normalized = normalizeFeishuConfig(payload?.config);
      setFeishuConfig(normalized);
      setDraft(draftFromFeishuConfig(normalized));
      setVisibleFields({});
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : t('channels.errors.loadFeishu'));
    } finally {
      setFeishuLoading(false);
    }
  }, [t]);

  const fetchXiaoyiConfig = useCallback(async () => {
    setXiaoyiLoading(true);
    setXiaoyiSaveError(null);
    setXiaoyiSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.xiaoyi.get_conf');
      const normalized = normalizeXiaoyiConfig(payload?.config);
      setXiaoyiConfig(normalized);
      setXiaoyiDraft(draftFromXiaoyiConfig(normalized));
      setXiaoyiVisibleFields({});
    } catch (err) {
      setXiaoyiSaveError(err instanceof Error ? err.message : t('channels.errors.loadXiaoyi'));
    } finally {
      setXiaoyiLoading(false);
    }
  }, [t]);

  const fetchDingtalkConfig = useCallback(async () => {
    setDingtalkLoading(true);
    setDingtalkSaveError(null);
    setDingtalkSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.dingtalk.get_conf');
      const normalized = normalizeDingtalkConfig(payload?.config);
      setDingtalkConfig(normalized);
      setDingtalkDraft(draftFromDingtalkConfig(normalized));
      setDingtalkVisibleFields({});
    } catch (err) {
      setDingtalkSaveError(err instanceof Error ? err.message : t('channels.errors.loadDingtalk'));
    } finally {
      setDingtalkLoading(false);
    }
  }, [t]);

  const fetchTelegramConfig = useCallback(async () => {
    setTelegramLoading(true);
    setTelegramSaveError(null);
    setTelegramSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.telegram.get_conf');
      const normalized = normalizeTelegramConfig(payload?.config);
      setTelegramConfig(normalized);
      setTelegramDraft(draftFromTelegramConfig(normalized));
      setTelegramVisibleFields({});
    } catch (err) {
      setTelegramSaveError(err instanceof Error ? err.message : t('channels.errors.loadTelegram'));
    } finally {
      setTelegramLoading(false);
    }
  }, [t]);

  const fetchDiscordConfig = useCallback(async () => {
    setDiscordLoading(true);
    setDiscordSaveError(null);
    setDiscordSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.discord.get_conf');
      const normalized = normalizeDiscordConfig(payload?.config);
      setDiscordConfig(normalized);
      setDiscordDraft(draftFromDiscordConfig(normalized));
      setDiscordVisibleFields({});
    } catch (err) {
      setDiscordSaveError(err instanceof Error ? err.message : t('channels.errors.loadDiscord'));
    } finally {
      setDiscordLoading(false);
    }
  }, [t]);

  const handleSelectChannel = useCallback(
    (channelId: SupportedChannelId) => {
      if (ADAPTING_CHANNEL_IDS.has(channelId)) {
        return;
      }
      setActiveChannelId(channelId);
    },
    [],
  );

  useEffect(() => {
    if (activeChannelId === 'feishu') {
      void fetchFeishuConfig();
      return;
    }
    if (activeChannelId === 'xiaoyi') {
      void fetchXiaoyiConfig();
      return;
    }
    if (activeChannelId === 'dingtalk') {
      void fetchDingtalkConfig();
      return;
    }
    if (activeChannelId === 'telegram') {
      void fetchTelegramConfig();
      return;
    }
    if (activeChannelId === 'discord') {
      void fetchDiscordConfig();
    }
  }, [activeChannelId, fetchDiscordConfig, fetchDingtalkConfig, fetchFeishuConfig, fetchTelegramConfig, fetchXiaoyiConfig]);

  const statusText = useMemo(() => {
    const enabledCount = channels.filter((channel) => channel.enabled).length;
    if (loadState === 'loading') {
      return t('common.loading');
    }
    if (loadState === 'error') {
      return t('channels.status.loadFailed');
    }
    return t('channels.status.enabledSummary', { enabledCount, total: channels.length });
  }, [channels, loadState, t]);

  const hasConfigChanges = useMemo(() => {
    const baseDraft = draftFromFeishuConfig(feishuConfig);
    return (
      baseDraft.enabled !== draft.enabled ||
      baseDraft.app_id !== draft.app_id ||
      baseDraft.app_secret !== draft.app_secret ||
      baseDraft.encrypt_key !== draft.encrypt_key ||
      baseDraft.verification_token !== draft.verification_token ||
      baseDraft.chat_id !== draft.chat_id ||
      normalizeAllowFromText(baseDraft.allow_from).join('\n') !== normalizeAllowFromText(draft.allow_from).join('\n')
    );
  }, [draft, feishuConfig]);
  const hasXiaoyiConfigChanges = useMemo(() => {
    const baseDraft = draftFromXiaoyiConfig(xiaoyiConfig);
    return (
      baseDraft.enabled !== xiaoyiDraft.enabled ||
      baseDraft.ak !== xiaoyiDraft.ak ||
      baseDraft.sk !== xiaoyiDraft.sk ||
      baseDraft.agent_id !== xiaoyiDraft.agent_id ||
      baseDraft.enable_streaming !== xiaoyiDraft.enable_streaming
    );
  }, [xiaoyiConfig, xiaoyiDraft]);
  const hasDingtalkConfigChanges = useMemo(() => {
    const baseDraft = draftFromDingtalkConfig(dingtalkConfig);
    return (
      baseDraft.enabled !== dingtalkDraft.enabled ||
      baseDraft.client_id !== dingtalkDraft.client_id ||
      baseDraft.client_secret !== dingtalkDraft.client_secret ||
      normalizeAllowFromText(baseDraft.allow_from).join('\n') !== normalizeAllowFromText(dingtalkDraft.allow_from).join('\n')
    );
  }, [dingtalkConfig, dingtalkDraft]);

  const hasTelegramConfigChanges = useMemo(() => {
    const baseDraft = draftFromTelegramConfig(telegramConfig);
    return (
      baseDraft.enabled !== telegramDraft.enabled ||
      baseDraft.bot_token !== telegramDraft.bot_token ||
      normalizeAllowFromText(baseDraft.allow_from).join('\n') !== normalizeAllowFromText(telegramDraft.allow_from).join('\n') ||
      baseDraft.parse_mode !== telegramDraft.parse_mode ||
      baseDraft.group_chat_mode !== telegramDraft.group_chat_mode
    );
  }, [telegramConfig, telegramDraft]);
  const hasDiscordConfigChanges = useMemo(() => {
    const baseDraft = draftFromDiscordConfig(discordConfig);
    return (
      baseDraft.enabled !== discordDraft.enabled ||
      baseDraft.bot_token !== discordDraft.bot_token ||
      baseDraft.application_id !== discordDraft.application_id ||
      baseDraft.guild_id !== discordDraft.guild_id ||
      baseDraft.channel_id !== discordDraft.channel_id ||
      normalizeAllowFromText(baseDraft.allow_from).join('\n') !== normalizeAllowFromText(discordDraft.allow_from).join('\n')
    );
  }, [discordConfig, discordDraft]);
  const handleFieldChange = <K extends keyof FeishuDraft>(key: K, value: FeishuDraft[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    if (saveError) {
      setSaveError(null);
    }
    if (success) {
      setSuccess(null);
    }
  };

  const handleCancelConfig = () => {
    if (!hasConfigChanges) return;
    setDraft(draftFromFeishuConfig(feishuConfig));
    setSaveError(null);
    setSuccess(null);
  };

  const toggleFieldVisible = (field: keyof FeishuDraft) => {
    setVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleXiaoyiFieldChange = <K extends keyof XiaoyiDraft>(key: K, value: XiaoyiDraft[K]) => {
    setXiaoyiDraft((prev) => ({ ...prev, [key]: value }));
    if (xiaoyiSaveError) {
      setXiaoyiSaveError(null);
    }
    if (xiaoyiSuccess) {
      setXiaoyiSuccess(null);
    }
  };

  const handleCancelXiaoyiConfig = () => {
    if (!hasXiaoyiConfigChanges) return;
    setXiaoyiDraft(draftFromXiaoyiConfig(xiaoyiConfig));
    setXiaoyiSaveError(null);
    setXiaoyiSuccess(null);
  };

  const toggleXiaoyiFieldVisible = (field: keyof XiaoyiDraft) => {
    setXiaoyiVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleDingtalkFieldChange = <K extends keyof DingTalkDraft>(key: K, value: DingTalkDraft[K]) => {
    setDingtalkDraft((prev) => ({ ...prev, [key]: value }));
    if (dingtalkSaveError) {
      setDingtalkSaveError(null);
    }
    if (dingtalkSuccess) {
      setDingtalkSuccess(null);
    }
  };

  const handleCancelDingtalkConfig = () => {
    if (!hasDingtalkConfigChanges) return;
    setDingtalkDraft(draftFromDingtalkConfig(dingtalkConfig));
    setDingtalkSaveError(null);
    setDingtalkSuccess(null);
  };

  const toggleDingtalkFieldVisible = (field: keyof DingTalkDraft) => {
    setDingtalkVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleTelegramFieldChange = <K extends keyof TelegramDraft>(key: K, value: TelegramDraft[K]) => {
    setTelegramDraft((prev) => ({ ...prev, [key]: value }));
    if (telegramSaveError) {
      setTelegramSaveError(null);
    }
    if (telegramSuccess) {
      setTelegramSuccess(null);
    }
  };

  const handleCancelTelegramConfig = () => {
    if (!hasTelegramConfigChanges) return;
    setTelegramDraft(draftFromTelegramConfig(telegramConfig));
    setTelegramSaveError(null);
    setTelegramSuccess(null);
  };

  const toggleTelegramFieldVisible = (field: keyof TelegramDraft) => {
    setTelegramVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleDiscordFieldChange = <K extends keyof DiscordDraft>(key: K, value: DiscordDraft[K]) => {
    setDiscordDraft((prev) => ({ ...prev, [key]: value }));
    if (discordSaveError) {
      setDiscordSaveError(null);
    }
    if (discordSuccess) {
      setDiscordSuccess(null);
    }
  };

  const handleCancelDiscordConfig = () => {
    if (!hasDiscordConfigChanges) return;
    setDiscordDraft(draftFromDiscordConfig(discordConfig));
    setDiscordSaveError(null);
    setDiscordSuccess(null);
  };

  const toggleDiscordFieldVisible = (field: keyof DiscordDraft) => {
    setDiscordVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleSaveConfig = async () => {
    if (!hasConfigChanges || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload = buildFeishuPayload(draft);
      const result = await webRequest<{ config?: unknown }>('channel.feishu.set_conf', payload);
      const normalized = normalizeFeishuConfig(result?.config);
      setFeishuConfig(normalized);
      setDraft(draftFromFeishuConfig(normalized));
      setSuccess(t('channels.saved.feishu'));
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : t('channels.errors.saveGeneric');
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveXiaoyiConfig = async () => {
    if (!hasXiaoyiConfigChanges || xiaoyiSaving) return;
    setXiaoyiSaving(true);
    setXiaoyiSaveError(null);
    try {
      const payload = buildXiaoyiPayload(xiaoyiDraft);
      const result = await webRequest<{ config?: unknown }>('channel.xiaoyi.set_conf', payload);
      const normalized = normalizeXiaoyiConfig(result?.config);
      setXiaoyiConfig(normalized);
      setXiaoyiDraft(draftFromXiaoyiConfig(normalized));
      setXiaoyiSuccess(t('channels.saved.xiaoyi'));
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : t('channels.errors.saveGeneric');
      setXiaoyiSaveError(message);
    } finally {
      setXiaoyiSaving(false);
    }
  };

  const handleSaveDingtalkConfig = async () => {
    if (!hasDingtalkConfigChanges || dingtalkSaving) return;
    setDingtalkSaving(true);
    setDingtalkSaveError(null);
    try {
      const payload = buildDingtalkPayload(dingtalkDraft);
      const result = await webRequest<{ config?: unknown }>('channel.dingtalk.set_conf', payload);
      const normalized = normalizeDingtalkConfig(result?.config);
      setDingtalkConfig(normalized);
      setDingtalkDraft(draftFromDingtalkConfig(normalized));
      setDingtalkSuccess(t('channels.saved.dingtalk'));
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : t('channels.errors.saveGeneric');
      setDingtalkSaveError(message);
    } finally {
      setDingtalkSaving(false);
    }
  };

  const handleSaveTelegramConfig = async () => {
    if (!hasTelegramConfigChanges || telegramSaving) return;
    setTelegramSaving(true);
    setTelegramSaveError(null);
    try {
      const payload = buildTelegramPayload(telegramDraft);
      const result = await webRequest<{ config?: unknown }>('channel.telegram.set_conf', payload);
      const normalized = normalizeTelegramConfig(result?.config);
      setTelegramConfig(normalized);
      setTelegramDraft(draftFromTelegramConfig(normalized));
      setTelegramSuccess(t('channels.saved.telegram'));
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : t('channels.errors.saveGeneric');
      setTelegramSaveError(message);
    } finally {
      setTelegramSaving(false);
    }
  };

  const handleSaveDiscordConfig = async () => {
    if (!hasDiscordConfigChanges || discordSaving) return;
    setDiscordSaving(true);
    setDiscordSaveError(null);
    try {
      const payload = buildDiscordPayload(discordDraft);
      const result = await webRequest<{ config?: unknown }>('channel.discord.set_conf', payload);
      const normalized = normalizeDiscordConfig(result?.config);
      setDiscordConfig(normalized);
      setDiscordDraft(draftFromDiscordConfig(normalized));
      setDiscordSuccess(t('channels.saved.discord'));
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : t('channels.errors.saveGeneric');
      setDiscordSaveError(message);
    } finally {
      setDiscordSaving(false);
    }
  };

  const isConfigRefreshing = feishuLoading || xiaoyiLoading || dingtalkLoading || telegramLoading || discordLoading;
  const configErrorNotice = useMemo(() => {
    return Array.from(
      new Set(
        [saveError, xiaoyiSaveError, dingtalkSaveError, telegramSaveError, discordSaveError].filter(
          (message): message is string => Boolean(message),
        ),
      ),
    ).join(t('common.and'));
  }, [discordSaveError, dingtalkSaveError, saveError, t, telegramSaveError, xiaoyiSaveError]);
  useEffect(() => {
    if (!configErrorNotice) {
      return;
    }
    const timer = window.setTimeout(() => {
      setSaveError(null);
      setXiaoyiSaveError(null);
      setDingtalkSaveError(null);
      setTelegramSaveError(null);
      setDiscordSaveError(null);
    }, 2000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [configErrorNotice]);

  return (
    <div className="flex-1 min-h-0 relative">
      <div className="card w-full h-full flex flex-col">
        {configErrorNotice ? (
          <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20">
            <div className="bg-danger text-white px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">
              {configErrorNotice}
            </div>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold">{t('channels.title')}</h2>
            <p className="text-sm text-text-muted mt-1">{t('channels.subtitle')}</p>
          </div>
          <div className="flex items-center gap-2" />
        </div>

        {error ? (
          <div className="border border-[var(--border-danger)] bg-danger-subtle rounded-lg p-4 text-sm text-danger flex items-center justify-between">
            <span>{t('channels.fetchFailed')}: {error}</span>
            <button onClick={() => void fetchChannels()} className="btn !px-3 !py-1.5">
              {t('channels.retry')}
            </button>
          </div>
        ) : (
          <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,3fr)_minmax(0,7fr)] gap-4">
            <section className="min-w-[260px] rounded-xl border border-border bg-card/70 backdrop-blur-sm shadow-sm flex flex-col min-h-0 overflow-hidden">
              <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-medium text-text">{t('channels.listTitle')}</h3>
                    <p className="text-xs text-text-muted mt-1 mono">
                      {t('channels.listMeta', { status: statusText, time: formatTime(lastUpdatedAt, i18n.language) })}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void fetchChannels()}
                    className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                    disabled={loadState === 'loading'}
                  >
                    {loadState === 'loading' ? t('common.refreshing') : t('common.refresh')}
                  </button>
                </div>
              </div>
              <div className="overflow-auto flex-1 min-h-0 p-3">
                {loadState === 'loading' ? (
                  <div className="space-y-2">
                    <div className="h-10 rounded-lg border border-border bg-secondary/40" />
                    <div className="h-10 rounded-lg border border-border bg-secondary/30" />
                  </div>
                ) : (
                  <div className="space-y-2">
                    {channels.map((channel, index) => {
                      const isAdapting = ADAPTING_CHANNEL_IDS.has(channel.channel_id);
                      const label = getChannelLabel(t, channel.channel_id);
                      return (
                        <button
                          type="button"
                          key={channel.channel_id}
                          onClick={() => handleSelectChannel(channel.channel_id)}
                          disabled={isAdapting}
                          className={`w-full rounded-xl border px-4 py-3.5 text-left transition-colors ${
                            isAdapting
                              ? 'channels-panel__channel-disabled border-border bg-card text-text-muted'
                              : activeChannelId === channel.channel_id
                                ? 'border-accent bg-accent-subtle text-text'
                                : 'border-border bg-card text-text hover:bg-bg-hover'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <span className="text-xs px-2.5 py-1 rounded-full border border-border bg-secondary text-text-muted font-medium">
                                #{index + 1}
                              </span>
                              <ChannelLogo channel={channel} label={label} />
                              <span className="text-sm font-medium text-text">{label}</span>
                              <span className="mono text-xs px-2.5 py-1 rounded-md border border-border bg-secondary text-text-muted">
                                {channel.channel_id}
                              </span>
                            </div>
                            <span
                              className={`text-xs px-2.5 py-1 rounded-full border font-medium ${
                                isAdapting
                                  ? 'text-text-muted border-border bg-secondary'
                                  : channel.enabled
                                    ? 'text-ok border-ok bg-ok-subtle'
                                    : 'text-text-muted border-border bg-secondary'
                              }`}
                            >
                              {isAdapting ? t('channels.status.adapting') : channel.enabled ? t('channels.status.enabled') : t('channels.status.disabled')}
                            </span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </section>

            <section className="min-h-0 flex">
                {activeChannelId === 'web' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center gap-3">
                        <ChannelHeaderLogo channelId="web" label={getChannelLabel(t, 'web')} />
                        <div>
                          <h4 className="text-sm font-medium text-text">{t('channels.config.webTitle')}</h4>
                          <p className="text-xs text-text-muted mt-1">{t('channels.config.webSubtitle')}</p>
                        </div>
                      </div>
                    </div>
                    <div className="p-4 text-sm text-text-muted flex-1 overflow-auto flex items-center justify-center text-center">
                      {t('channels.config.webEmpty')}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'xiaoyi' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="xiaoyi" label={getChannelLabel(t, 'xiaoyi')} />
                          <div>
                            <h4 className="text-sm font-medium text-text">{t('channels.config.xiaoyiTitle')}</h4>
                            <p className="text-xs text-text-muted mt-1">{t('channels.config.xiaoyiSubtitle')}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchXiaoyiConfig()}
                            disabled={xiaoyiSaving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {xiaoyiLoading ? t('common.refreshing') : t('common.refresh')}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelXiaoyiConfig}
                            disabled={!hasXiaoyiConfigChanges || xiaoyiSaving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {t('common.cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveXiaoyiConfig()}
                            disabled={!hasXiaoyiConfigChanges || xiaoyiSaving || !isConnected}
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {xiaoyiSaving ? t('common.saving') : t('common.save')}
                          </button>
                        </div>
                      </div>
                    </div>

                    {xiaoyiSuccess ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {xiaoyiSuccess}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {xiaoyiLoading ? (
                        <div className="text-sm text-text-muted">{t('channels.loading.xiaoyi')}</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={xiaoyiDraft.enabled}
                                  onClick={() => handleXiaoyiFieldChange('enabled', !xiaoyiDraft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    xiaoyiDraft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      xiaoyiDraft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            {(['ak', 'sk', 'agent_id'] as const).map((field) => (
                              <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <div className="relative">
                                    <input
                                      type={isSensitiveXiaoyiField(field) && !xiaoyiVisibleFields[field] ? 'password' : 'text'}
                                      value={xiaoyiDraft[field]}
                                      onChange={(e) => handleXiaoyiFieldChange(field, e.target.value)}
                                      placeholder={t('channels.placeholders.configValue')}
                                      className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                        isSensitiveXiaoyiField(field) ? 'pr-10' : ''
                                      }`}
                                    />
                                    {isSensitiveXiaoyiField(field) ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleXiaoyiFieldVisible(field)}
                                        className="channels-panel__visibility-toggle"
                                        aria-label={xiaoyiVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                        title={xiaoyiVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                      >
                                        <VisibilityIcon visible={Boolean(xiaoyiVisibleFields[field])} />
                                      </button>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'dingtalk' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="dingtalk" label={getChannelLabel(t, 'dingtalk')} />
                          <div>
                            <h4 className="text-sm font-medium text-text">{t('channels.config.dingtalkTitle')}</h4>
                            <p className="text-xs text-text-muted mt-1">{t('channels.config.dingtalkSubtitle')}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchDingtalkConfig()}
                            disabled={dingtalkSaving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {dingtalkLoading ? t('common.refreshing') : t('common.refresh')}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelDingtalkConfig}
                            disabled={!hasDingtalkConfigChanges || dingtalkSaving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {t('common.cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveDingtalkConfig()}
                            disabled={
                              !hasDingtalkConfigChanges ||
                              dingtalkSaving ||
                              !isConnected ||
                              !dingtalkDraft.client_id.trim() ||
                              !dingtalkDraft.client_secret.trim()
                            }
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {dingtalkSaving ? t('common.saving') : t('common.save')}
                          </button>
                        </div>
                      </div>
                    </div>

                    {dingtalkSuccess ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {dingtalkSuccess}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {dingtalkLoading ? (
                        <div className="text-sm text-text-muted">{t('channels.loading.dingtalk')}</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={dingtalkDraft.enabled}
                                  onClick={() => handleDingtalkFieldChange('enabled', !dingtalkDraft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    dingtalkDraft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      dingtalkDraft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            {(['client_id', 'client_secret'] as const).map((field) => (
                              <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <div className="relative">
                                    <input
                                      type={isSensitiveDingtalkField(field) && !dingtalkVisibleFields[field] ? 'password' : 'text'}
                                      value={dingtalkDraft[field]}
                                      onChange={(e) => handleDingtalkFieldChange(field, e.target.value)}
                                      placeholder={field === 'client_id' ? t('channels.placeholders.appId') : t('channels.placeholders.appSecret')}
                                      className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                        isSensitiveDingtalkField(field) ? 'pr-10' : ''
                                      }`}
                                    />
                                    {isSensitiveDingtalkField(field) ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleDingtalkFieldVisible(field)}
                                        className="channels-panel__visibility-toggle"
                                        aria-label={dingtalkVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                        title={dingtalkVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                      >
                                        <VisibilityIcon visible={Boolean(dingtalkVisibleFields[field])} />
                                      </button>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-top mono text-xs text-text-muted w-[32%]">allow_from</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <textarea
                                  value={dingtalkDraft.allow_from}
                                  onChange={(e) => handleDingtalkFieldChange('allow_from', e.target.value)}
                                  placeholder={t('channels.placeholders.employeeIds')}
                                  rows={4}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent resize-y"
                                />
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'feishu' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="feishu" label={getChannelLabel(t, 'feishu')} />
                          <div>
                            <h4 className="text-sm font-medium text-text">{t('channels.config.feishuTitle')}</h4>
                            <p className="text-xs text-text-muted mt-1">{t('channels.config.feishuSubtitle')}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchFeishuConfig()}
                            disabled={saving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {feishuLoading ? t('common.refreshing') : t('common.refresh')}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelConfig}
                            disabled={!hasConfigChanges || saving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {t('common.cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveConfig()}
                            disabled={!hasConfigChanges || saving || !isConnected || !draft.app_id.trim() || !draft.app_secret.trim()}
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving ? t('common.saving') : t('common.save')}
                          </button>
                        </div>
                      </div>
                    </div>

                    {success ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {success}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {feishuLoading ? (
                        <div className="text-sm text-text-muted">{t('channels.loading.feishu')}</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={draft.enabled}
                                  onClick={() => handleFieldChange('enabled', !draft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    draft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      draft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            {(['app_id', 'app_secret', 'encrypt_key', 'verification_token', 'chat_id'] as const).map((field) => (
                              <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <div className="relative">
                                    <input
                                      type={isSensitiveField(field) && !visibleFields[field] ? 'password' : 'text'}
                                      value={draft[field]}
                                      onChange={(e) => handleFieldChange(field, e.target.value)}
                                      placeholder={field === 'chat_id' ? t('channels.placeholders.chatId') : t('channels.placeholders.configValue')}
                                      className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                        isSensitiveField(field) ? 'pr-10' : ''
                                      }`}
                                    />
                                    {isSensitiveField(field) ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleFieldVisible(field)}
                                        className="channels-panel__visibility-toggle"
                                        aria-label={visibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                        title={visibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                      >
                                        <VisibilityIcon visible={Boolean(visibleFields[field])} />
                                      </button>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-top mono text-xs text-text-muted w-[32%]">allow_from</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <textarea
                                  value={draft.allow_from}
                                  onChange={(e) => handleFieldChange('allow_from', e.target.value)}
                                  placeholder={t('channels.placeholders.ids')}
                                  rows={4}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent resize-y"
                                />
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'telegram' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="telegram" label="Telegram" />
                          <div>
                            <h4 className="text-sm font-medium text-text">Telegram 频道参数配置</h4>
                            <p className="text-xs text-text-muted mt-1">配置 Telegram Bot 服务相关参数</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchTelegramConfig()}
                            disabled={telegramSaving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {telegramLoading ? '刷新中...' : '刷新'}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelTelegramConfig}
                            disabled={!hasTelegramConfigChanges || telegramSaving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveTelegramConfig()}
                            disabled={!hasTelegramConfigChanges || telegramSaving || !isConnected}
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {telegramSaving ? '保存中...' : '保存'}
                          </button>
                        </div>
                      </div>
                    </div>

                    {telegramSuccess ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {telegramSuccess}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {telegramLoading ? (
                        <div className="text-sm text-text-muted">正在加载 Telegram 配置...</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={telegramDraft.enabled}
                                  onClick={() => handleTelegramFieldChange('enabled', !telegramDraft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    telegramDraft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      telegramDraft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">bot_token</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <div className="relative">
                                  <input
                                    type={telegramVisibleFields['bot_token'] ? 'text' : 'password'}
                                    value={telegramDraft.bot_token}
                                    onChange={(e) => handleTelegramFieldChange('bot_token', e.target.value)}
                                    placeholder="请输入 Bot Token（从 @BotFather 获取）"
                                    className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent pr-10"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => toggleTelegramFieldVisible('bot_token')}
                                    className="channels-panel__visibility-toggle"
                                    aria-label={telegramVisibleFields['bot_token'] ? '隐藏明文' : '显示明文'}
                                    title={telegramVisibleFields['bot_token'] ? '隐藏明文' : '显示明文'}
                                  >
                                    <VisibilityIcon visible={Boolean(telegramVisibleFields['bot_token'])} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-top mono text-xs text-text-muted w-[32%]">allow_from</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <textarea
                                  value={telegramDraft.allow_from}
                                  onChange={(e) => handleTelegramFieldChange('allow_from', e.target.value)}
                                  placeholder="每行一个 Telegram user_id（也支持逗号分隔）"
                                  rows={4}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent resize-y"
                                />
                              </td>
                            </tr>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">parse_mode</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <select
                                  value={telegramDraft.parse_mode}
                                  onChange={(e) => handleTelegramFieldChange('parse_mode', e.target.value)}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
                                >
                                  <option value="Markdown">Markdown</option>
                                  <option value="HTML">HTML</option>
                                  <option value="None">None</option>
                                </select>
                              </td>
                            </tr>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">group_chat_mode</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <select
                                  value={telegramDraft.group_chat_mode}
                                  onChange={(e) => handleTelegramFieldChange('group_chat_mode', e.target.value)}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent"
                                >
                                  <option value="mention">仅响应 @提及 (mention)</option>
                                  <option value="reply">仅响应回复 (reply)</option>
                                  <option value="all">响应所有消息 (all)</option>
                                  <option value="off">禁用群聊 (off)</option>
                                </select>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'discord' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="discord" label={getChannelLabel(t, 'discord')} />
                          <div>
                            <h4 className="text-sm font-medium text-text">{t('channels.config.discordTitle')}</h4>
                            <p className="text-xs text-text-muted mt-1">{t('channels.config.discordSubtitle')}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchDiscordConfig()}
                            disabled={discordSaving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {discordLoading ? t('common.refreshing') : t('common.refresh')}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelDiscordConfig}
                            disabled={!hasDiscordConfigChanges || discordSaving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {t('common.cancel')}
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveDiscordConfig()}
                            disabled={
                              !hasDiscordConfigChanges ||
                              discordSaving ||
                              !isConnected ||
                              !discordDraft.bot_token.trim()
                            }
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {discordSaving ? t('common.saving') : t('common.save')}
                          </button>
                        </div>
                      </div>
                    </div>

                    {discordSuccess ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {discordSuccess}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {discordLoading ? (
                        <div className="text-sm text-text-muted">{t('channels.loading.discord')}</div>
                      ) : (
                        <>
                          <div className="mb-3 rounded-md border border-border bg-secondary/20 px-3 py-2 text-xs text-text-muted">
                            {t('channels.config.discordHint')}
                          </div>
                          <table className="w-full text-sm">
                            <tbody>
                              <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                                <td className="px-4 py-2.5 align-middle">
                                  <button
                                    type="button"
                                    role="switch"
                                    aria-checked={discordDraft.enabled}
                                    onClick={() => handleDiscordFieldChange('enabled', !discordDraft.enabled)}
                                    className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                      discordDraft.enabled ? 'bg-ok' : 'bg-secondary'
                                    }`}
                                  >
                                    <span
                                      className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                        discordDraft.enabled ? 'translate-x-4' : 'translate-x-0'
                                      }`}
                                    />
                                  </button>
                                </td>
                              </tr>
                              {(['bot_token', 'application_id', 'guild_id', 'channel_id'] as const).map((field) => (
                                <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                  <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                  <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                    <div className="relative">
                                      <input
                                        type={isSensitiveDiscordField(field) && !discordVisibleFields[field] ? 'password' : 'text'}
                                        value={discordDraft[field]}
                                        onChange={(e) => handleDiscordFieldChange(field, e.target.value)}
                                        placeholder={t('channels.placeholders.configValue')}
                                        className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                          isSensitiveDiscordField(field) ? 'pr-10' : ''
                                        }`}
                                      />
                                      {isSensitiveDiscordField(field) ? (
                                        <button
                                          type="button"
                                          onClick={() => toggleDiscordFieldVisible(field)}
                                          className="channels-panel__visibility-toggle"
                                          aria-label={discordVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                          title={discordVisibleFields[field] ? t('channels.hideValue') : t('channels.showValue')}
                                        >
                                          <VisibilityIcon visible={Boolean(discordVisibleFields[field])} />
                                        </button>
                                      ) : null}
                                    </div>
                                  </td>
                                </tr>
                              ))}
                              <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-top mono text-xs text-text-muted w-[32%]">allow_from</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <textarea
                                    value={discordDraft.allow_from}
                                    onChange={(e) => handleDiscordFieldChange('allow_from', e.target.value)}
                                    placeholder={t('channels.placeholders.ids')}
                                    rows={4}
                                    className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent resize-y"
                                  />
                                </td>
                              </tr>
                            </tbody>
                          </table>
                        </>
                      )}
                    </div>
                  </div>
                ) : null}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
