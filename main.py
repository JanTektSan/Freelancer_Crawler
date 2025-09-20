from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Set
import httpx
import asyncio
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import os
from datetime import datetime
import logging
from contextlib import asynccontextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pydantic models
class UserInfo(BaseModel):
    user_id: int
    username: str
    country: str
    created_at: Optional[datetime] = None

class UserRequest(BaseModel):
    user_ids: List[int]

class UserResponse(BaseModel):
    users: List[UserInfo]
    total_count: int

# Global queue and processing state
user_queue: asyncio.Queue = None
processing_users: Set[int] = set()
queue_lock = asyncio.Lock()

# MongoDB connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "user_crawler")
COLLECTION_NAME = "users"

try:
    client = MongoClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    collection = db[COLLECTION_NAME]
    
    # Create index on user_id for faster queries
    collection.create_index("user_id", unique=True)
    logger.info("Connected to MongoDB successfully")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

# Freelancer API base URL
FREELANCER_API_BASE = "https://www.freelancer.com/api/users/0.1/users"

class QueuedUserService:
    
    @staticmethod
    async def get_user_from_db(user_id: int) -> Optional[UserInfo]:
        """Get user info from database"""
        try:
            user_doc = collection.find_one({"user_id": user_id})
            if user_doc:
                return UserInfo(
                    user_id=user_doc["user_id"],
                    username=user_doc["username"],
                    country=user_doc["country"],
                    created_at=user_doc.get("created_at")
                )
            return None
        except Exception as e:
            logger.error(f"Error getting user {user_id} from DB: {e}")
            return None

    @staticmethod
    async def get_user_from_freelancer_api(user_id: int) -> Optional[UserInfo]:
        """Get user info from Freelancer API"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{FREELANCER_API_BASE}/{user_id}"
                response = await client.get(url)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Extract user info from API response
                    if "result" in data and "users" in data["result"]:
                        users = data["result"]["users"]
                        if users and len(users) > 0:
                            user_data = users[0]
                            
                            # Extract username and country
                            username = user_data.get("username", "")
                            
                            # Country can be in different places in the response
                            country = ""
                            if "location" in user_data and "country" in user_data["location"]:
                                country = user_data["location"]["country"].get("name", "")
                            elif "country" in user_data:
                                country = user_data["country"].get("name", "")
                            
                            return UserInfo(
                                user_id=user_id,
                                username=username,
                                country=country,
                                created_at=datetime.utcnow()
                            )
                
                logger.warning(f"Failed to get user {user_id} from Freelancer API: Status {response.status_code}")
                return None
                
        except httpx.TimeoutException:
            logger.error(f"Timeout when fetching user {user_id} from Freelancer API")
            return None
        except Exception as e:
            logger.error(f"Error fetching user {user_id} from Freelancer API: {e}")
            return None

    @staticmethod
    async def save_user_to_db(user_info: UserInfo) -> bool:
        """Save user info to database"""
        try:
            user_doc = {
                "user_id": user_info.user_id,
                "username": user_info.username,
                "country": user_info.country,
                "created_at": user_info.created_at or datetime.utcnow()
            }
            
            collection.insert_one(user_doc)
            logger.info(f"Saved user {user_info.user_id} to database")
            return True
            
        except DuplicateKeyError:
            logger.warning(f"User {user_info.user_id} already exists in database")
            return True  # Still consider as success since data exists
        except Exception as e:
            logger.error(f"Error saving user {user_info.user_id} to DB: {e}")
            return False

    @staticmethod
    async def add_to_queue_if_needed(user_id: int) -> bool:
        """Add user to queue if not already processing"""
        async with queue_lock:
            if user_id not in processing_users:
                processing_users.add(user_id)
                await user_queue.put(user_id)
                logger.info(f"Added user {user_id} to processing queue")
                return True
            else:
                logger.info(f"User {user_id} already in processing queue")
                return False

    @staticmethod
    async def wait_for_user_data(user_id: int, max_retries: int = 10, retry_delay: float = 1.0) -> Optional[UserInfo]:
        """Wait for user data to appear in database (for queued users)"""
        for attempt in range(max_retries):
            user_info = await QueuedUserService.get_user_from_db(user_id)
            if user_info:
                logger.info(f"User {user_id} data found after {attempt + 1} attempts")
                return user_info
            
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                logger.debug(f"Waiting for user {user_id} data, attempt {attempt + 1}/{max_retries}")
        
        logger.warning(f"User {user_id} data not found after {max_retries} attempts")
        return None

    @staticmethod
    async def get_user_info(user_id: int) -> Optional[UserInfo]:
        """Get user info with queue-based optimization"""
        # First check database
        user_info = await QueuedUserService.get_user_from_db(user_id)
        if user_info:
            logger.info(f"Found user {user_id} in database")
            return user_info
        
        # Check if user is already being processed
        async with queue_lock:
            is_processing = user_id in processing_users
        
        if is_processing:
            # Wait for the processing to complete
            logger.info(f"User {user_id} is being processed, waiting...")
            return await QueuedUserService.wait_for_user_data(user_id)
        else:
            # Add to queue and wait
            await QueuedUserService.add_to_queue_if_needed(user_id)
            logger.info(f"User {user_id} added to queue, waiting for processing...")
            return await QueuedUserService.wait_for_user_data(user_id)

async def queue_worker():
    """Background worker to process user queue"""
    logger.info("Queue worker started")
    
    while True:
        try:
            # Get user ID from queue (wait indefinitely)
            user_id = await user_queue.get()
            logger.info(f"Processing user {user_id} from queue")
            
            try:
                # Attempt to get user info from API
                user_info = await QueuedUserService.get_user_from_freelancer_api(user_id)
                
                if user_info:
                    # Try to save to database with retries
                    max_save_retries = 3
                    save_success = False
                    
                    for save_attempt in range(max_save_retries):
                        save_success = await QueuedUserService.save_user_to_db(user_info)
                        if save_success:
                            logger.info(f"Successfully processed user {user_id}")
                            break
                        else:
                            logger.warning(f"Failed to save user {user_id} to database, attempt {save_attempt + 1}/{max_save_retries}")
                            if save_attempt < max_save_retries - 1:
                                await asyncio.sleep(1)  # Wait 1 second before retry
                    
                    if not save_success:
                        logger.error(f"Failed to save user {user_id} to database after {max_save_retries} attempts")
                        # Don't re-queue since we have the data from API - this is a persistent DB issue
                else:
                    logger.warning(f"Failed to get user {user_id} from API, re-queuing")
                    # Re-queue for retry (with delay to avoid immediate retry)
                    await asyncio.sleep(5)  # Wait 5 seconds before retry
                    await user_queue.put(user_id)
                    continue
                    
            except Exception as e:
                logger.error(f"Error processing user {user_id}: {e}")
                # Re-queue for retry
                await asyncio.sleep(5)
                await user_queue.put(user_id)
                continue
            
            finally:
                # Remove from processing set when we have user_info (successful API call)
                # regardless of database save result, since DB issues shouldn't cause re-queuing
                if user_info:
                    async with queue_lock:
                        processing_users.discard(user_id)
                
                # Mark task as done only if we got user_info (successful API call)
                # If API call failed, we re-queued, so don't mark as done
                if user_info:
                    user_queue.task_done()
                
        except asyncio.CancelledError:
            logger.info("Queue worker cancelled")
            break
        except Exception as e:
            logger.error(f"Unexpected error in queue worker: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    global user_queue
    
    # Startup
    user_queue = asyncio.Queue(maxsize=1000)  # Limit queue size to prevent memory issues
    
    # Start queue worker
    worker_task = asyncio.create_task(queue_worker())
    logger.info("Application started with queue worker")
    
    yield
    
    # Shutdown
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info("Application shutdown complete")

# FastAPI app with lifespan management
app = FastAPI(title="User Info API with Queue", version="1.1.0", lifespan=lifespan)

@app.get("/")
async def root():
    """Health check endpoint"""
    queue_size = user_queue.qsize() if user_queue else 0
    processing_count = len(processing_users)
    
    return {
        "message": "User Info API with Queue is running", 
        "status": "healthy",
        "queue_size": queue_size,
        "processing_count": processing_count
    }

@app.post("/api/users", response_model=UserResponse)
async def get_users_info(request: UserRequest):
    """
    Get user information for multiple user IDs using queue optimization
    
    Args:
        request: UserRequest containing list of user IDs
    
    Returns:
        UserResponse with user information
    """
    if not request.user_ids:
        raise HTTPException(status_code=400, detail="user_ids array cannot be empty")
    
    if len(request.user_ids) > 100:  # Reasonable limit to prevent abuse
        raise HTTPException(status_code=400, detail="Maximum 100 user IDs allowed per request")
    
    logger.info(f"Processing request for {len(request.user_ids)} users")
    
    # Create tasks for concurrent processing
    tasks = [QueuedUserService.get_user_info(user_id) for user_id in request.user_ids]
    
    # Execute all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    users = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Error processing user {request.user_ids[i]}: {result}")
        elif result is not None:
            users.append(result)
        else:
            logger.warning(f"No data found for user {request.user_ids[i]}")
    
    return UserResponse(
        users=users,
        total_count=len(users)
    )

@app.get("/api/users/{user_id}", response_model=UserInfo)
async def get_single_user_info(user_id: int):
    """
    Get information for a single user
    
    Args:
        user_id: The user ID to get information for
    
    Returns:
        UserInfo object
    """
    user_info = await QueuedUserService.get_user_info(user_id)
    
    if not user_info:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    
    return user_info

@app.get("/api/stats")
async def get_stats():
    """Get database and queue statistics"""
    try:
        total_users = collection.count_documents({})
        queue_size = user_queue.qsize() if user_queue else 0
        processing_count = len(processing_users)
        
        return {
            "total_users_cached": total_users,
            "database": DATABASE_NAME,
            "collection": COLLECTION_NAME,
            "queue_size": queue_size,
            "processing_count": processing_count,
            "currently_processing": list(processing_users)
        }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Error getting statistics")

@app.get("/api/queue")
async def get_queue_info():
    """Get detailed queue information"""
    queue_size = user_queue.qsize() if user_queue else 0
    processing_count = len(processing_users)
    
    return {
        "queue_size": queue_size,
        "processing_count": processing_count,
        "currently_processing": list(processing_users),
        "queue_available": user_queue is not None
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
