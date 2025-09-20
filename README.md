# User Info API with Queue Optimization

A FastAPI server that efficiently fetches user information from freelancer.com API with intelligent queue-based deduplication and MongoDB caching.

## 🚀 Features

- **Queue-Based Deduplication**: Eliminates duplicate API calls for users already being processed
- **MongoDB Caching**: Stores user data to avoid repeated external API requests
- **Background Processing**: Dedicated worker processes queue items asynchronously
- **Retry Logic**: Automatically retries failed API calls with delays
- **Concurrent Support**: Handles multiple simultaneous requests efficiently
- **Real-time Monitoring**: Track queue status and processing statistics

## 📋 Prerequisites

- **Python 3.8+**
- **MongoDB** running on localhost:27017
- **Internet connection** for freelancer.com API access

## ⚡ Quick Start

### 1. Setup Environment
```bash
# Activate virtual environment
myvenv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)
Create `.env` file from template:
```bash
copy .env.template .env
```

### 3. Start MongoDB
Ensure MongoDB is running:
```bash
# Windows service (if installed as service)
net start MongoDB

# Or manually
mongod
```

### 4. Start the Server
```bash
python main.py
```

Server will start on `http://localhost:8000`

## 📡 API Endpoints

### Core API

#### Get Multiple Users (Queue-Optimized)
```http
POST /api/users
Content-Type: application/json

{
    "user_ids": [88205665, 12345, 67890]
}
```

**Response:**
```json
{
    "users": [
        {
            "user_id": 88205665,
            "username": "john_doe", 
            "country": "United States",
            "created_at": "2024-01-15T10:30:00Z"
        }
    ],
    "total_count": 1
}
```

#### Get Single User
```http
GET /api/users/{user_id}
```

### Monitoring Endpoints

#### Health Check with Queue Status
```http
GET /
```

Returns:
```json
{
    "message": "User Info API with Queue is running",
    "status": "healthy", 
    "queue_size": 2,
    "processing_count": 1
}
```

#### Queue Information
```http
GET /api/queue
```

#### Statistics
```http
GET /api/stats
```

### Interactive Documentation
Visit `http://localhost:8000/docs` for Swagger UI

## 🔧 Configuration

### Environment Variables (.env file)
```env
# MongoDB Configuration  
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=user_crawler

# API Configuration
FREELANCER_API_BASE=https://www.freelancer.com/api/users/0.1/users
```

### Default Settings
- **Max Users per Request**: 100
- **Queue Size Limit**: 1000
- **API Timeout**: 30 seconds
- **Retry Delay**: 5 seconds
- **Wait Timeout**: 10 seconds (1 second intervals)

## 🏗️ How It Works

### Queue Optimization Logic

1. **Request Processing**: 
   - Check if user exists in MongoDB cache
   - If cached: Return immediately
   - If not cached: Check processing queue

2. **Queue Management**:
   - If user already in queue: Wait for completion
   - If not in queue: Add to queue and wait

3. **Background Worker**:
   - Processes queue items one by one
   - Calls freelancer.com API: `https://www.freelancer.com/api/users/0.1/users/{user_id}`
   - Extracts username and country from response
   - Saves to MongoDB for future use

4. **Retry Logic**:
   - Failed API calls: Re-queue with 5-second delay
   - Failed database saves: Retry up to 3 times

### Data Flow
```
Request → MongoDB Check → Queue Check → Add to Queue → Background Worker
    ↓           ↓              ↓              ↓              ↓
Response ← Return Data ← Wait for Data ← Processing ← API Call + Save
```

## 📊 Performance Benefits

### Without Queue Optimization
```
Request A: [1, 2, 3] → 3 API calls
Request B: [1, 4, 5] → 3 API calls (duplicate for user 1)
Total: 6 API calls
```

### With Queue Optimization  
```
Request A: [1, 2, 3] → 3 API calls
Request B: [1, 4, 5] → 2 API calls (user 1 waits for completion)
Total: 5 API calls (16% reduction)
```

## 🧪 Testing

### Basic Test
```bash
# Health check
curl http://localhost:8000/

# Get users
curl -X POST http://localhost:8000/api/users \
  -H "Content-Type: application/json" \
  -d '{"user_ids": [88205665, 12345]}'

# Check statistics  
curl http://localhost:8000/api/stats
```

### Test Queue Optimization
Send multiple concurrent requests with overlapping user IDs:
```python
import httpx
import asyncio

async def test_optimization():
    async with httpx.AsyncClient() as client:
        # Send concurrent requests with overlapping user IDs
        tasks = [
            client.post("http://localhost:8000/api/users", 
                       json={"user_ids": [88205665, 12345]}),
            client.post("http://localhost:8000/api/users", 
                       json={"user_ids": [88205665, 67890]}),
        ]
        results = await asyncio.gather(*tasks)
        return results

asyncio.run(test_optimization())
```

## 🔍 Monitoring

### Key Metrics
- **queue_size**: Current items waiting in queue
- **processing_count**: Users currently being processed
- **total_users_cached**: Total users stored in MongoDB
- **currently_processing**: List of user IDs being processed

### Log Messages
The server provides detailed logging:
```
INFO:main:Added user 12345 to processing queue
INFO:main:Processing user 12345 from queue  
INFO:main:Saved user 12345 to database
INFO:main:User 88205665 data found after 2 attempts
```

## 🔧 Troubleshooting

### Common Issues

**"MongoDB connection failed"**
```bash
# Check if MongoDB is running
mongod --version
net start MongoDB
```

**"Queue not processing"**
- Check server logs for worker status
- Verify internet connectivity
- Check freelancer.com API availability

**"Slow response times"**
- First requests may be slower (API calls + database saves)
- Subsequent requests for same users will be fast (cached)
- Use `/api/queue` to monitor processing status

### Performance Tips

1. **Batch requests**: Send multiple user IDs in single request
2. **Monitor queue**: Check `/api/queue` endpoint for processing status
3. **Cache utilization**: Repeated user IDs serve from MongoDB instantly
4. **Concurrent requests**: System handles overlapping requests efficiently

## 📁 Project Structure

```
C:\Users\Administrator\Work\Crawler\
├── main.py              # FastAPI server with queue system
├── requirements.txt     # Python dependencies  
├── README.md           # This documentation
├── .env.template       # Configuration template
├── .env                # Your configuration (optional)
└── myvenv\             # Virtual environment
```

## 🛡️ Error Handling

The system handles various error scenarios:

- **API failures**: Automatic retry with delays
- **Database connection issues**: Retry logic for saves
- **Invalid user IDs**: Graceful handling and logging
- **Queue overflow**: Limited to 1000 items maximum
- **Timeout errors**: 30-second timeout with proper cleanup

## 📈 API Response Codes

- **200**: Success
- **400**: Invalid request (empty user_ids, too many users)
- **404**: User not found
- **500**: Internal server error

## 🔐 Data Schema

### MongoDB Document
```json
{
    "_id": ObjectId("..."),
    "user_id": 88205665,
    "username": "john_doe",
    "country": "United States", 
    "created_at": "2024-01-15T10:30:00Z"
}
```

### API Response Model
```json
{
    "users": [
        {
            "user_id": 88205665,
            "username": "john_doe",
            "country": "United States",
            "created_at": "2024-01-15T10:30:00Z"
        }
    ],
    "total_count": 1
}
```

## 📄 License

This project is for demonstration purposes. Please respect freelancer.com's API terms of service and rate limits.
