from jiuwenclaw.schema.event_base import EventBase


class GatewayEvents(EventBase):
    """Gateway 和 AgentServer 交互事件

    这些事件定义了 Gateway 与 AgentServer 之间的消息传递生命周期。
    """

    scope: str = "gateway"

    GATEWAY_STARTED = EventBase.get_event("gateway_started")
    GATEWAY_STOPPED = EventBase.get_event("gateway_stopped")


class AgentServerEvents(EventBase):
    """AgentServer 事件

    这些事件定义了 AgentServer 的内部事件。
    """

    scope: str = "agent_server"

    AGENT_SERVER_STARTED = EventBase.get_event("agent_server_started")
    AGENT_SERVER_STOPPED = EventBase.get_event("agent_server_stopped")
