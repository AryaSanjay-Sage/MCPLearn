import os #gives OS dependent functionality (here, copy environment variables and make file paths)
import sys #accesses command line arguments and exits the script
import asyncio #python library for writing concurrent code using async/await syntax. MCP uses asynchronous comm to handle network operations and i/o (RESEARCH THIS)
from typing import Optional #Used for type hinting
from contextlib import AsyncExitStack  #research what a context manager is. "async with" W "multiple context managers" -> AsyncExitStack makes sure they're all entered 
#and exited even if errors occur. This is good because MCP Client session and stdio transport are asynhronous resources need to be carefully shutdown

from mcp import ClientSession, StdioServerParameters #importing the clients connection to the server, handling sending requests and getting responses according to MCP standard
#defining how the MCP should launch and communication with the server through a different process instead of standard input/output (stdio)
##RESEARCH: How does the MCP communicate server through that different process? Similarities/differences between that and input/output?
from mcp.client.stdio import stdio_client

from anthropic import Anthropic #importxs main anthropic client class, lets you interact with Claude models
##Likely will need to use this since we're using Claude/AWS bedrock!
from dotenv import load_dotenv #library which helps manage env variables from the .env file

load_dotenv()  # load environment variables from .env

print("client.py is running! Imports loaded successfully.")

class MCPClient: #class that has all logic/state to MCP client application
    def __init__(self):
        #Called whenever you make a new instance of MCPClient, or when client = MCPClient()
        self.session: Optional[ClientSession] = None #Once client successfully connects to MCP server holds the ClientSession object
        #Optional part is indicating that the type could be ClientSession or None
        self.exit_stack = AsyncExitStack() #Manages life cycle of async. context managers- when this is called it makes sure that all stack's entered resources get properly shit down
        self.anthropic = Anthropic() #Makes instance of Anthropic client, which interacts with Anthropic's Claude. 
        ##NOTED: This automatically looks for that ANTHROPIC_API_KEY environment variable (loaded w load_dotenv()) to authenticate with API

    #RESEARCH Coroutine (special type of function defined using async def syntax), in async. programming, when coroutine function is called it returns a coroutine object, which is an awaitable object 
    async def connect_to_server(self, server_script_path: str):
        """Connects to the Multi Client Protocol Server and contains the
        
        Args:
            server_script_path: Holds the path to the .py or .js server script
        """
        is_python = server_script_path.endswith('.py')
        is_js = server_script_path.endswith('.js')
        if not (is_python or is_js):
            raise ValueError("The script isn't formatted properly with .py or .js! Fix pls")
        
        command = "python" if is_python else "node" #use python or node command based on whether its a py or js file
        #You either say python + filename or node + filename in the terminal based on the type of file it is
        env = os.environ.copy() #Makes copy of curr process's env vars so we modify the env for the server subprocess instead of changing the client's own environment
        ##RESEARCH: Make visual into the client process and server subprocess relationship for better understanding
        venv_site_packages = os.path.join(sys.prefix, 'Lib', 'site-packages')
        #sys.prefix points to .venv or env base directory, lib sitepackages is the standard loc for installed pkges in python windows venv
        if 'PYTHONPATH' in env: #If the pythonpath alr exists in env copy
            env['PYTHONPATH'] = f"{venv_site_packages}{os.pathsep}{env['PYTHONPATH']}" #prepends client site-packages path with os.pathsep. 
            ##Correctly provides path separator for current OS (based on windows/Unix), makes sure server's python interpreter searchs client's venv packages first
            ##Research os.pathsep to understand!!
        else:
            env['PYTHONPATH'] = venv_site_packages #otherwise, sets it to the client's site-packages path
        
        #Makes an instance of StdioServerParameters 
        server_params = StdioServerParameters(
            command=command, #Command to run the server, like "python"
            args=[server_script_path], #list of arguments passed to command, starting with server script path
            env=env #passes in modified env with the pythonpath adjustment from earlier! tells mcp to launch server subprocess with this specific env
        )

        #Research asynchronous context managers in python!!
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        ##stdio_client takes those params and handles launching the server process and setting up comm standard input/output streams #RESEARCH!!
        ##enters the stdio_client context manager - basically running the setup code of stdio_client and adding to asyncexitstack
        ###makes sure that when .aclose() method is called later during cleanup, the stdio client will be properly exited, closing the pipes and stopping the server process
        ###result is stdio_transport = tuple of (read_stream, write_stream)
        self.stdio, self.write = stdio_transport
        ##unpacks transport tuple into parts to read/write from/to server
        self.session = await self.exit_stack.enter_async_context(ClientSession(self.stdio, self.write))
        ##Gives MCP ClientSession instance standard stdio_client I/O streams
        ###ClientSession is a high-level interface to send MCP requests and receive responses
        ###await part enters the ClientSession context manager and adds it to the exit stack for proper asynch cleanup
        await self.session.initialize() #Sends initialize request to MCP server, client/server establish capabilities

        #after init, client asks and server responses w list of available tools that it exposes (research what "EXPOSES" is referencing)
        response = await self.session.list_tools()
        tools = response.tools #gets list of tool objects from response #RESEARCH WHAT THESE TOOL OBJECTS LOOK LIKE
        print("\nConnected to server with tools:", [tool.name for tool in tools]) #prints the names of the tools server advertised, confirming successful connection and tool discovery

    async def process_query(self, query: str) -> str:
        """Process a query using Claude and available tools"""
        messages = [
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        available_tools = [{
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema
        } for tool in response.tools]

        # Initial Claude API call
        response = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            messages=messages,
            tools=available_tools
        )

        # Process response and handle tool calls
        final_text = []

        assistant_message_content = []
        for content in response.content:
            if content.type == 'text':
                final_text.append(content.text)
                assistant_message_content.append(content)
            elif content.type == 'tool_use':
                tool_name = content.name
                tool_args = content.input

                # Execute tool call
                result = await self.session.call_tool(tool_name, tool_args)
                final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")

                assistant_message_content.append(content)
                messages.append({
                    "role": "assistant",
                    "content": assistant_message_content
                })
                messages.append({
                    "role": "user",
                    "content": [
                    {
                            "type": "tool_result",
                            "tool_use_id": content.id,
                            "content": result.content
                    }
                    ]
                })

                # Get next response from Claude
                response = self.anthropic.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    messages=messages,
                    tools=available_tools
                )

                final_text.append(response.content[0].text)

            return "\n".join(final_text)

    async def chat_loop(self):
        """Run an interactive chat loop"""
        print("\nMCP Client Started!")
        print("Type your queries or 'quit' to exit.")

        while True:
            try:
                query = input("\nQuery: ").strip()

                if query.lower() == 'quit':
                    break

                response = await self.process_query(query)
                print("\n" + response)

            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()

async def main():
    if len(sys.argv) < 2:
        print("Usage: python client.py <path_to_server_script>")
        sys.exit(1)

    client = MCPClient()
    try:
        print("Attempting to connect to server...") # <-- ADD THIS
        await client.connect_to_server(sys.argv[1])
        print("Server connection successful. Starting chat loop...") # <-- ADD THIS
        await client.chat_loop()
    except Exception as e: # <-- ADD THIS BLOCK
        print(f"\nFATAL CLIENT STARTUP ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        print("Cleaning up resources...") # <-- ADD THIS
        await client.cleanup()

if __name__ == "__main__":
    asyncio.run(main())