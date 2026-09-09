from reasoner import BaseReasoner


class RuleReasoner(BaseReasoner):

    async def decide(
        self,
        task,
        observations,
        tools,
    ):
        if isinstance(observations, dict):
            pytest_result = observations.get("pytest_result", {})
            code = observations.get("calculator_code", "")
        else:
            pytest_result = {}
            code = ""
            for observation in observations:
                if observation.get("action") == "read_text":
                    code = str(observation.get("result", {}).get("content", ""))
                if observation.get("action") == "run_process":
                    pytest_result = observation.get("result", {}).get(
                        "structured_content", {}
                    )


        if (
            pytest_result
            and pytest_result.get("returncode") != 0
            and "return a - b" in code
        ):

            return {
                "action":"replace_text",
                "arguments":{
                    "path":
                    "agent_test/calculator.py",

                    "old":
                    "return a - b",

                    "new":
                    "return a + b"
                }
            }


        return {
            "action":"finish",
            "arguments":{}
        }
