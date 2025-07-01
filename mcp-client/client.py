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
        tools = response.tools #gets list of tool objects from response #these are the tools we made in the server code!
        print("\nConnected to server with tools:", [tool.name for tool in tools]) #prints the names of the tools server advertised, confirming successful connection and tool discovery

    async def process_query(self, query: str) -> str:
        """Using Claude and any other tools that are available, process a query"""
        messages = [ #Initializes Claude conversation history, each obj defines role (who said it) and content (what was said)
            {
                "role": "user",
                "content": query
            }
        ]

        response = await self.session.list_tools()
        #Resource to reference: https://modelcontextprotocol.io/docs/concepts/tools 
        available_tools = [{
            "name": tool.name, 
            "description" : tool.description, 
            "input_schema": tool.inputSchema
        } for tool in response.tools] #gets connected MCP server's tools and formats them into list of dictionaries, formatted according to anthropic's message's api tool parameter expectations
        #https://docs.anthropic.com/en/api/messages
        #https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-anthropic-claude-messages.html 

        #Calls Anthropic's Claude API
        response = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000, #max number of tokens claude can generate in response 
            ##Interestingly, when you google this it says claude 3.5 sonnet can have a max of 4,096 tokens. Is 1000 better to use for applications to ensure you use less memory? Can play around with this number
            ###Potential ideas here: cost control (prevents unnecessarily long outputs), latency management (better response time for real-time applications), avoid incomplete responses, more focused and concist
            messages = messages, #gives current conversation history to claude
            tools = available_tools #tell claude about functions it can call, claude decides if it needs a tool to answer the query
        )

        final_text = [] #list to get parts of final response that will get shown to user
        assistant_manage_content = [] #list to keep all of the assistant's parts of the message (text/tool_use) which get sent back to claude if a tool is used
        #RESEARCH: What does this 'assistant' exactly mean? Where and how does it relate to the server code?
        for content in response.content: #in the case when claude just gives a direct text response (doesn't need a tool or is done with tool use)
            if content.type == 'text':
                final_text.append(content.text) #adds text to the output list
                assistant_manage_content.append(content) #adds text content to assistant's turn
                ##Research into assistant's turn meaning, and what does "more turns needed" means? Is this when LLMs regenerate responses within their response or self correct, or something else?
            elif content.type == 'tool_use':
                tool_name = content.name #name of tool claude wants to call
                tool_args = content.input #args claude gives for the tool call, like a dictionary
                result = await self.session.call_tool(tool_name, tool_args) #sends call_tool request to mcp server w claude's tool name & args given by claude
                ##would execute one of our server's functions, like get_forecast, and result has the output from the server
                final_text.append(f"[Calling tool {tool_name} with args {tool_args}]")
                ##adds msge to output that is human readable for transparency to let user know what tool is being called (can see this in claude)

                assistant_manage_content.append(content) #adds tool_use block (content obj generated by claude, whre it says to answer this i need to run funct X with args Y) to assistants turn 
                #Tool use content block: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use
                messages.append({ #Assistants turn with tool_use, adds assistance turn to convo history, including tool_use block telling claude what action it took
                    "role": "assistant", 
                    "content": assistant_manage_content
                })
                messages.append({ #users turn w tool_result - add "tool_result" message to conversation history
                    # Reference my "intermediary or an agent between the human user and the AI model (Claude) and the MCP server (which provides the tools)" OneNote notes for this part to understand
                    "role": "user", 
                    "content": [
                    {
                            "type": "tool_result", 
                            "tool_use_id": content.id,
                            "content": result.content
                    }
                    ]
                })

                #after tool result, entire convo history (including tool call and result) sent back to claude
                #claude makes a new response (might be final answer) or another tool call. loop continues until claude gives text response
                response = self.anthropic.messages.create( 
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    messages=messages, 
                    tools=available_tools
                )

                final_text.append(response.content[0].text) #adds in the text from claude's prev response

        return "\n".join(final_text) #joins all collected text + tool call msges into single string, separated by newlines, and returns into chatloop

    async def chat_loop(self): #async method implements interactive chat interface
        """Implements the interactive chat interface loop"""
        print("\nMCP Client started!")
        print("Type your queries or 'quit' to exit.")

        while True: #infinite loop so user can put in multiple queries
            try: #handles if anything goes wrong lik enetwork issue, claude api error, or tool call unexpected error
                query = input("\nQuery: ").strip() #prompts user to put in a query, reading line from stdin and .strip() removes any whitespace from beginning or end

                if query.lower() == 'quit':
                    break #case insensitive, if user types in quit ends the chat sesh

                response = await self.process_query(query) #calls process_query method w user input. since process_query is an async method, needs to use await keyword bc needs to be awaited
                print("\n" + response) #print the response from process_query (which has claude answer and any tool call msges
            except Exception as e:
                print(f"\nError: {str(e)}")

    async def cleanup(self): #async method responsible for shutting down client and releasing resources
        """Clean up resources"""
        await self.exit_stack.aclose() #key line for cleanup - closes the standard i/o pipes to server, stops server process, and cleans up mcp client sesh

async def main():
    if len(sys.argv) < 2: #sys.argv is a list in python w command line args passed into script. [0] index just has the script name
        print("Usage: python client.py <path_to_server_script>") #if less than two args provided, (script expects one arg path to serever script), if it's less than 2 then the req server path wasn't given
        sys.exit(1) #exits script w error code, since 1 indicates error
    client = MCPClient()
    try:
        print("Trying to connect to server...")
        await client.connect_to_server(sys.argv[1]) #calls connect_to_server method to get connection to the server
        ##[1] is the path to the server script - check if there's a [2] or if this only ever has a [0] or [1] index
        print("Server connection successful. Starting chat loop...")
        await client.chat_loop() #after connected, this puts in the client into interactive chat loop so they can send queries
    except Exception as e:
        print(f"\nFATAL CLIENT STARTUP ERROR: {str(e)}") #prints user friendly error msge
        import traceback
        traceback.print_exc()
    finally:
        print("Cleaning up resources...")
        await client.cleanup() #calls cleanup method to make sure all resources are properly closed

if __name__ == "__main__":
    asyncio.run(main())