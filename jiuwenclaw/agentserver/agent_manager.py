# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


from jiuwenclaw.agentserver.interface import JiuWenClaw


class AgentManager:
    def __init__(self):
        self.agents = {}

    async def initialize(self):
        return

    async def prepare_agent(self, session_id, *args):
        agent = JiuWenClaw()
        await agent.create_instance()
        self.agents["default_session"] = agent

    def get_agent(self, session_id, *args):
        return self.agents["default_session"]

