from dotenv import load_dotenv
from ionworks import Ionworks

load_dotenv()

ionworks = Ionworks()

print(ionworks.health_check())
