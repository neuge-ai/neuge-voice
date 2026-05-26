import asyncio
import os
from dotenv import load_dotenv
from nextgen_voice_agent.voice.llm_router import OrchestratorLLMProvider
from nextgen_voice_agent.config import get_settings

async def main():
    load_dotenv()

    provider = OrchestratorLLMProvider()
    provider.model = "groq/qwen/qwen3-32b"
    
    prompts = [
        "what's the weather gonna be in calcutta tomorrow?",
        "write a python script to check the weather",
        "search the web for the weather in calcutta",
        "hello, who are you?"
    ]
    
    print(f"Testing LLM Router with model: {provider.model}")
    print("-" * 50)
    
    for prompt in prompts:
        print(f"\nUser: {prompt}")
        try:
            decision = await provider.route_turn(
                user_text=prompt,
                system_state="You are idle.",
                conversation_history=[]
            )
            print(f"Decision: {decision}")
        except Exception as e:
            print(f"CRASH: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
