import pandas as pd
import numpy as np
import tensorflow as tf
from tensorflow.keras.layers import Input, Embedding, Flatten, Dot, Dense, Add
from tensorflow.keras.models import Model, load_model
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.metrics import mean_squared_error
import googlemaps
import math

train = 0

# Load dataset
place = pd.read_csv('Data/tourism_with_id.csv')
rating = pd.read_csv('Data/tourism_rating.csv')
user = pd.read_csv('Data/user.csv')
data_image = pd.read_csv('Data/tourism_data_img.csv')

# Ensure that the 'Place_Ratings' and 'Rating' columns are numeric
rating['Place_Ratings'] = pd.to_numeric(rating['Place_Ratings'], errors='coerce')  # Convert to numeric, coercing errors to NaN
place['Rating'] = pd.to_numeric(place['Rating'], errors='coerce')  # Similarly for place['Rating']

# Calculate the median 'Time_Minutes' for each location
city_median = place.groupby('City')['Time_Minutes'].median()

# Check if there are any missing 'City' values
missing_cities = place[place['City'].isnull()]
if not missing_cities.empty:
    print(f"Warning: There are missing 'City' values in the 'place' dataset:\n{missing_cities}")

# Fill in the blank value based on the median location
place['Time_Minutes'] = place.apply(
    lambda row: city_median.get(row['City'], row['Time_Minutes']) if pd.isnull(row['Time_Minutes']) else row['Time_Minutes'],
    axis=1
)

# Combining the dataset
rating = pd.read_csv('Data/tourism_rating.csv')
merge_data = pd.merge(rating, place[['Place_Id', 'Rating', 'Place_Name', 'Description', 'Category', 'City', 'Price', 'Time_Minutes', 'Coordinate', 'Lat', 'Long']], on='Place_Id', how='left')

# Calculate the mean rating for each place
merge_data = merge_data.groupby('Place_Id').agg(
    Mean_Rating=('Place_Ratings', 'mean'),
    Rating=('Rating', 'first'),
    Place_Name=('Place_Name', 'first'),
    Description=('Description', 'first'),
    Category=('Category', 'first'),
    City=('City', 'first'),
    Price=('Price', 'first'),
    Time_Minutes=('Time_Minutes', 'first'),
    Coordinate=('Coordinate', 'first'),
    Lat=('Lat', 'first'),
    Long=('Long', 'first')
).reset_index()

merged_final = pd.merge(
    merge_data,
    data_image[['Place_Id', 'image_url']],  # Include only relevant columns from data_image
    on='Place_Id',  # Column to merge on
    how='left'      # Use 'left' to keep all rows from merge_data
)

# Display the first few rows of the 'place' dataframe
print(place.head())

user_ids = rating['User_Id'].unique().tolist()
place_ids = place['Place_Id'].unique().tolist()

user_id_to_index = {user_id: index for index, user_id in enumerate(user_ids)}
place_id_to_index = {place_id: index for index, place_id in enumerate(place_ids)}

rating['User_Index'] = rating['User_Id'].map(user_id_to_index)
rating['Place_Index'] = rating['Place_Id'].map(place_id_to_index)

num_users = len(user_ids)
num_places = len(place_ids)
embedding_size = 50

user_input = Input(shape=(1,))
user_embedding = Embedding(num_users, embedding_size, embeddings_regularizer=tf.keras.regularizers.l2(1e-6))(user_input)
user_vec = Flatten()(user_embedding)

place_input = Input(shape=(1,))
place_embedding = Embedding(num_places, embedding_size, embeddings_regularizer=tf.keras.regularizers.l2(1e-6))(place_input)
place_vec = Flatten()(place_embedding)

dot_product = Dot(axes=1)([user_vec, place_vec])

# Add bias terms for users and places
user_bias = Embedding(num_users, 1)(user_input)
user_bias = Flatten()(user_bias)

place_bias = Embedding(num_places, 1)(place_input)
place_bias = Flatten()(place_bias)

prediction = Add()([dot_product, user_bias, place_bias])

cf_model = Model([user_input, place_input], prediction)
cf_model.compile(optimizer='adam', loss='mean_squared_error')

def load_or_train_cf_model():
    model_path = 'Model/cf_model.h5'
    
    try:
        # Try loading the pre-trained model
        cf_model = load_model(model_path)
    except:
        # If model does not exist, train it
        print("Model not found, training a new model...")
        cf_model.fit(
            [rating['User_Index'], rating['Place_Index']],
            rating['Place_Ratings'],
            epochs=20,
            verbose=1
        )
        # Save the model after training
        cf_model.save(model_path)  # Save the model for future use
    return cf_model

def predict_ratings(user_id, place_ids):
    user_index = user_id_to_index[user_id]
    place_indices = [place_id_to_index[place_id] for place_id in place_ids if place_id in place_id_to_index]
    predictions = cf_model.predict([np.array([user_index] * len(place_indices)), np.array(place_indices)])
    return predictions.flatten()

# Vectorizer di luar fungsi, dapat digunakan di berbagai bagian
vectorizer = TfidfVectorizer(stop_words='english')

# Cosine similarity di tingkat global, belum dihitung
cosine_sim = None

def calculate_cbf_scores(filtered_places):
    """
    Calculate content-based filtering (CBF) scores for the places based on categories or descriptions.

    Args:
        filtered_places: DataFrame of places to filter based on categories.

    Returns:
        A DataFrame of places with their CBF scores and other relevant details.
    """
    global cosine_sim  # Menyatakan bahwa cosine_sim adalah variabel global

    # Check if filtered_places is empty
    if filtered_places.empty:
        print("Warning: No places found after filtering!")
        return pd.DataFrame(columns=['Place_Id', 'name', 'category', 'similarity_score'])

    # Ensure 'Features' column creation works
    filtered_places['Features'] = filtered_places['Place_Name'] + ' ' + filtered_places['Category']

    try:
        # Vektorisasi fitur menggunakan TF-IDF
        tfidf_matrix = vectorizer.fit_transform(filtered_places['Features'])
        cosine_sim = cosine_similarity(tfidf_matrix, tfidf_matrix)  # Hitung cosine similarity
    except ValueError as e:
        print(f"Error in vectorization: {e}")
        print(f"Unique features: {filtered_places['Features'].unique()}")
        return pd.DataFrame(columns=['Place_Id', 'name', 'category', 'similarity_score'])

    # For each place, recommend places with highest similarity scores (top 10)
    recommendations = []
    for idx in range(len(filtered_places)):
        num_similar = min(10, len(filtered_places))  # Avoid out-of-bounds indices
        similar_indices = cosine_sim[idx].argsort()[-(num_similar -1):][::-1]  # Get top N similar places

        for similar_idx in similar_indices:
            if similar_idx != idx:  # Avoid recommending the same place
                recommendations.append({
                    'Place_Id': filtered_places.iloc[similar_idx]['Place_Id'],
                    'name': filtered_places.iloc[similar_idx]['Place_Name'],
                    'category': filtered_places.iloc[similar_idx]['Category'],
                    'similarity_score': cosine_sim[idx][similar_idx]
                })

    # If no recommendations found
    if not recommendations:
        print("No recommendations could be generated!")
        return pd.DataFrame(columns=['Place_Id', 'name', 'category', 'similarity_score'])

    return pd.DataFrame(recommendations)

# Function to calculate distance using Google Maps API
def calculate_distance(start_lat, start_lng, end_lat, end_lng):
    """
    Calculate the great-circle distance between two points on Earth using the Haversine formula.
    Returns the distance in kilometers and an estimated travel time in minutes.
    """
    try:
        # Convert latitude and longitude from degrees to radians
        start_lat_rad = math.radians(start_lat)
        start_lng_rad = math.radians(start_lng)
        end_lat_rad = math.radians(end_lat)
        end_lng_rad = math.radians(end_lng)

        # Radius of the Earth in kilometers
        earth_radius_km = 6371.0

        # Haversine formula
        dlat = end_lat_rad - start_lat_rad
        dlng = end_lng_rad - start_lng_rad
        a = math.sin(dlat / 2)**2 + math.cos(start_lat_rad) * math.cos(end_lat_rad) * math.sin(dlng / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        # Calculate the distance
        distance_km = earth_radius_km * c

        # Estimate travel time assuming an average speed (e.g., 60 km/h)
        average_speed_kmh = 60  # You can adjust this value based on your scenario
        travel_time_minutes = (distance_km / average_speed_kmh) * 60

        return distance_km, travel_time_minutes

    except Exception as e:
        print(f"Error calculating distance between coordinates: ({start_lat}, {start_lng}) and ({end_lat}, {end_lng})")
        print(f"Error details: {str(e)}")
        return None

def map_city_to_airport(city):
    """Map input city to full airport name"""
    airport_mapping = {
        'Jakarta': 'Bandar Udara Internasional Soekarno Hatta',
        'Yogyakarta': 'Bandar Udara Internasional Yogyakarta',
        'Semarang': 'Bandar Udara Jenderal Ahmad Yani',
        'Surabaya': 'Bandar Udara Internasional Juanda',
        'Singapore': 'Bandar Udara Internasional Changi Singapura',
        'Palembang': 'Bandar Udara Internasional Sultan Mahmud Badaruddin II',
        'Balikpapan': 'Bandar Udara Internasional Sultan Aji Muhammad Sulaiman Sepinggan Balikpapan',
        'Merauke': 'Bandar Udara Mopah',
        'Jakarta (Halim Perdanakusuma)': 'Bandar Udara Internasional Halim Perdanakusuma',
        'Banjarmasin': 'Bandar Udara Internasional Syamsudin Noor',
        'Jayapura': 'Bandar Udara Sentani',
        'Denpasar': 'Bandara Internasional I Gusti Ngurah Rai',
        'Makassar': 'Bandar Udara Internasional Sultan Hasanuddin',
        'Banda Aceh': 'Bandar Udara Internasional Sultan Iskandar Muda'
    }
    return airport_mapping.get(city, city)

flights_data = pd.read_csv('Data/flightsCapstone_cleaned.csv')

def filter_flights(departure_city, destination_city, max_budget=None):
    # Convert city names to full airport names
    departure_airport = map_city_to_airport(departure_city)
    destination_airport = map_city_to_airport(destination_city)

    filtered_flights = flights_data[
        (flights_data['departure_airport_name'] == departure_airport) &
        (flights_data['arrival_airport_name'] == destination_airport)
    ]

    if max_budget:
        filtered_flights = filtered_flights[filtered_flights['price'] <= max_budget]

    return filtered_flights.sort_values(by='price')

def filter_places(city=None, categories=None):
    """
    Filter places based on city and categories.

    Args:
        city: City name to filter places (optional).
        categories: List of categories to filter places (optional).

    Returns:
        Filtered DataFrame.
    """
    print(f"Filtering places - City: {city}, Categories: {categories}")

    filtered = merged_final.copy()
    if city:
        filtered = filtered[filtered['City'].str.contains(city, case=False, na=False)]

    if categories:
        print(f"Categories before filtering: {filtered['Category'].unique()}")
        filtered = filtered[filtered['Category'].isin(categories)]
        print(f"Categories after filtering: {filtered['Category'].unique()}")
        print(f"Number of places after category filtering: {len(filtered)}")

    return filtered

# Tambahkan kode ini di dalam fungsi utama rekomendasi
def recommend_tourist_destinations(
    user_id, user_lat, user_lng, user_city, user_categories,
    days=None, time=8, budget=None, is_new_user=False,
    departure_city=None, destination_city=None

    ):

    """
    Recommend tourist destinations with sequential distance calculation,
    resetting to original starting point each day.

    Args:
        user_id: The ID of the user for whom the recommendations are being made.
        user_lat (float): Latitude of the user's starting location.
        user_lng (float): Longitude of the user's starting location.
        user_city (str): City preference for the user.
        user_categories: Categories filter (if any).
        days: Number of days for splitting recommendations (if applicable).
        time (float): Fixed daily time limit set to 8 hours.
        budget (float): Budget preference for the user (if applicable).

    Returns:
        A list of recommended destinations for each day, total time used, and total budget spent.
    """

    # Rekomendasi penerbangan
    recommended_flights = pd.DataFrame()  # Default empty DataFrame
    if departure_city and destination_city:
      recommended_flights = filter_flights(departure_city, destination_city, budget)

      # If no flights found within budget, try without budget constraint
      if recommended_flights.empty and budget:
          recommended_flights = filter_flights(departure_city, destination_city)

    # Check if existing user
    user_exists = user_id in rating['User_Id'].unique()

    # Determine categories
    if user_categories is None:
      if user_exists:
        # Get user's past ratings
        user_ratings = rating[rating['User_Id'] == user_id]

        # Merge ratings with place data to get categories
        user_rated_places = pd.merge(rating, merged_final[['Place_Id', 'Category']], on='Place_Id', how='left')

        # Count category frequencies and sort
        category_counts = user_rated_places['Category'].value_counts().reset_index()
        category_counts.columns = ['Category', 'Frequency']

        # Get the most frequent category
        user_categories = category_counts.head(2)['Category'].tolist()
        print(f"User's most frequent category: {user_categories}")

      else:
        city_places = merged_final[merged_final['City'].str.contains(user_city, case=False, na=False)]
        # Group by category and calculate mean rating
        category_ratings = city_places.groupby('Category')['Rating'].mean().sort_values(ascending=False)
        # Select top 3 categories with highest average ratings in the city
        user_categories = category_ratings.head(3).index.tolist()

        print(f"New user - selecting top-rated categories in {user_city}:")
        print(category_ratings)
        print(f"Selected categories: {user_categories}")

    # Ensure category is a list
    if not isinstance(user_categories, list):
      user_categories = [user_categories]

    print(f"Final categories: {user_categories}")

    # Step 1: Filter places based on city and categories (if provided)
    filtered_places = filter_places(
        city=user_city,
        categories=user_categories
        )

    # Step 2: Get Content-Based Filtering recommendations
    cbf_recommendations = calculate_cbf_scores(filtered_places)

    # Step 3: Get Collaborative Filtering recommendations
    place_ids = cbf_recommendations['Place_Id'].unique()

    if user_exists:
        cf_recommendations = predict_ratings(user_id, place_ids)

        # Convert to DataFrame
        cf_recommendations = pd.DataFrame({'Place_Id': place_ids, 'cf_rating': cf_recommendations})

    else:
        # Calculate weighted global average ratings
        global_avg_ratings = merged_final.groupby('Place_Id')['Rating'].mean()

        cf_recommendations = pd.DataFrame({
            'Place_Id': place_ids,
            'cf_rating': [
                global_avg_ratings.get(pid, merged_final['Rating'].mean()) + np.random.uniform(-0.5, 0.5)
                for pid in place_ids
                ]
        })

    # Convert to DataFrame
    cbf_recommendations = pd.DataFrame(cbf_recommendations)

    # Step 4: Combine recommendations
    combined_recommendations = pd.merge(
        cbf_recommendations,
        merged_final[['Place_Id', 'Rating', 'Time_Minutes', 'Price', 'Lat', 'Long', 'image_url']],
        on='Place_Id',
        how='left'
        )
    combined_recommendations = pd.merge(
        combined_recommendations,
        cf_recommendations,
        on='Place_Id',
        how='left'
        )

    # Calculate MSE between CBF and CF recommendations
    combined_recommendations['mse'] = (
        combined_recommendations['Rating'] - combined_recommendations['cf_rating'])**2

    # Sort recommendations by MSE to prioritize consistent recommendations
    combined_recommendations = combined_recommendations.sort_values('mse')

    # Remove duplicates, if any
    combined_recommendations = combined_recommendations.drop_duplicates(subset='Place_Id')

    # Track recommendations per day
    recommendations_per_day = []
    total_time_per_day = []
    total_budget_per_day = []

    # Track visited places across days
    visited_places = set()

    if days:
        for day in range(days):
            day_recommendations = []
            day_total_time = 0
            day_total_budget = 0

            # IMPORTANT: Reset to original starting point for each day
            current_lat = user_lat
            current_lng = user_lng

            # Iterate through sorted recommendations
            for _, place in combined_recommendations.iterrows():
                # Skip if place has been visited in previous days
                if place['Place_Id'] in visited_places:
                    continue

                # Calculate distance from current location to this destination
                distance_km, travel_time = calculate_distance(current_lat, current_lng, place['Lat'], place['Long'])

                # Calculate total time for this place (travel time + visit time in hours)
                place_total_time = (place['Time_Minutes'] / 60) + (travel_time / 60)

                # Check if adding this place would exceed 8-hour limit
                if day_total_time + place_total_time > time:
                    continue

                # Check budget constraint if provided
                if budget and day_total_budget + place['Price'] > budget:
                    continue

                # Add place to daily recommendations
                place_with_distance = place.copy()
                place_with_distance['distance_km'] = distance_km
                place_with_distance['travel_time'] = travel_time
                day_recommendations.append(place_with_distance)

                # Update tracking variables
                day_total_time += place_total_time
                day_total_budget += place['Price']

                # Update current location for next distance calculation
                current_lat = place['Lat']
                current_lng = place['Long']

                # Mark place as visited
                visited_places.add(place['Place_Id'])

            # Convert to DataFrame
            day_recommendations_df = pd.DataFrame(day_recommendations)
            recommendations_per_day.append(day_recommendations_df)
            total_time_per_day.append(day_total_time)
            total_budget_per_day.append(day_total_budget)

            # Calculate MSE
            mse = combined_recommendations['mse'].mean()

    return recommendations_per_day, total_time_per_day, total_budget_per_day, mse, recommended_flights

from flask import Flask, request, jsonify

app = Flask(__name__)
port = "4000"


# ... Update inbound traffic via APIs to use the public-facing ngrok URL


# Define Flask routes
@app.route('/recommend', methods=['POST'])
def recommend():
    try:
        data = request.get_json()

        # Extract parameters from the POST request
        user_id = data.get('user_id')
        user_lat = data.get('user_lat')
        user_lng = data.get('user_lng')
        user_city = data.get('user_city')
        user_categories = data.get('user_categories')
        days = data.get('days', None)
        time = data.get('time', 8)
        budget = data.get('budget', None)
        departure_city = data.get('departure_city', None) 
        is_new_user = data.get('is_new_user', False)
        destination_city = data.get('destination_city', None)

        # Get recommendations
        recommendations_per_day, total_time_per_day, total_budget_per_day, mse, recommended_flights = recommend_tourist_destinations(
            user_id, user_lat, user_lng, user_city, user_categories, days, time, budget, False, departure_city, destination_city
        )
        recommendations_per_day, total_time_per_day, total_budget_per_day, mse, recommended_flights = recommend_tourist_destinations(
            user_id, user_lat, user_lng, user_city, user_categories,
            days, time, budget, is_new_user, departure_city, destination_city
        )

        # Convert recommendations to JSON-serializable format
        recommendations_json = []
        for day_recommendations in recommendations_per_day:
            recommendations_json.append(day_recommendations.to_dict(orient='records'))

        flights_json = recommended_flights.to_dict(orient='records')

        response_data = {
            'recommendations': recommendations_json,
            'total_time_per_day': total_time_per_day,
            'total_budget_per_day': total_budget_per_day,
            'mse': mse,
            'recommended_flights': flights_json
        }


        return jsonify(response_data), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
app.run(debug=False, host='0.0.0.0', port=8080)