from django.shortcuts import render, redirect, get_object_or_404
from .models import Movie, Theater, Seat, Booking
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum, Count
from django.contrib.admin.views.decorators import staff_member_required

import stripe

# ======================
# STRIPE SETUP
# ======================
stripe.api_key = settings.STRIPE_SECRET_KEY


def movie_list(request):
    search_query = request.GET.get('search')
    genre = request.GET.get('genre')
    language = request.GET.get('language')

    movies = Movie.objects.all()

    if search_query:
        movies = movies.filter(name__icontains=search_query)

    if genre:
        movies = movies.filter(genre__iexact=genre)

    if language:
        movies = movies.filter(language__iexact=language)

    genres = Movie.objects.values_list('genre', flat=True).distinct()
    languages = Movie.objects.values_list('language', flat=True).distinct()

    return render(request, 'movies/movie_list.html', {
        'movies': movies,
        'genres': genres,
        'languages': languages,
        'selected_genre': genre,
        'selected_language': language,
    })


def theater_list(request, movie_id):
    movie = get_object_or_404(Movie, id=movie_id)
    theaters = Theater.objects.filter(movie=movie)
    return render(request, 'movies/theater_list.html', {
        'movie': movie,
        'theaters': theaters
    })


@login_required(login_url='/login/')
def book_seats(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)

    seats = Seat.objects.filter(theater=theater)
    for seat in seats:
        seat.release_if_expired()

    seats = Seat.objects.filter(theater=theater)

    if request.method == 'POST':
        selected_seats = request.POST.getlist('seats')

        if not selected_seats:
            return render(request, "movies/seat_selection.html", {
                'theater': theater,
                'seats': seats,
                'error': "No seat selected"
            })

        seat_price = 200
        total_amount = seat_price * len(selected_seats)

        for seat_id in selected_seats:
            seat = get_object_or_404(Seat, id=seat_id, theater=theater)

            if seat.is_booked or seat.is_reserved:
                return render(request, "movies/seat_selection.html", {
                    'theater': theater,
                    'seats': seats,
                    'error': "One or more seats are no longer available."
                })

            seat.is_reserved = True
            seat.reserved_by = request.user
            seat.reserved_at = timezone.now()
            seat.save()

        request.session['selected_seats'] = selected_seats
        request.session['theater_id'] = theater.id
        request.session['amount'] = total_amount

        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'inr',
                    'unit_amount': total_amount * 100,
                    'product_data': {
                        'name': f'{theater.movie.name} Tickets',
                    },
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=request.build_absolute_uri('/movies/payment-success/'),
            cancel_url=request.build_absolute_uri('/movies/payment-cancel/'),
        )

        return redirect(checkout_session.url)

    return render(request, 'movies/seat_selection.html', {
        'theater': theater,
        'seats': seats
    })


@login_required
def payment_success(request):
    selected_seats = request.session.get('selected_seats')
    theater_id = request.session.get('theater_id')
    total_amount = request.session.get('amount')

    theater = get_object_or_404(Theater, id=theater_id)
    booked_seat_numbers = []

    for seat_id in selected_seats:
        seat = get_object_or_404(Seat, id=seat_id, theater=theater)

        if seat.is_reserved and seat.reserved_by == request.user:
            Booking.objects.create(
                user=request.user,
                seat=seat,
                movie=theater.movie,
                theater=theater,
                price=total_amount / len(selected_seats)
            )

            seat.is_booked = True
            seat.is_reserved = False
            seat.reserved_by = None
            seat.reserved_at = None
            seat.save()

            booked_seat_numbers.append(seat.seat_number)

    if request.user.email:
        send_mail(
            subject="🎟 Your Movie Ticket is Confirmed!",
            message=(
                f"Hello {request.user.username},\n\n"
                f"Your booking has been successfully confirmed.\n\n"
                f"🎬 Movie: {theater.movie.name}\n"
                f"🏢 Theater: {theater.name}\n"
                f"⏰ Show Time: {theater.time}\n"
                f"💺 Seats: {', '.join(booked_seat_numbers)}\n\n"
                f"Enjoy your movie 🍿\n\n"
                f"Regards,\n"
                f"BookMySeat Team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[request.user.email],
            fail_silently=False,
        )

    return render(request, 'movies/payment_success.html')


@login_required
def payment_cancel(request):
    return render(request, 'movies/payment_cancel.html')


# ======================
# ADMIN DASHBOARD
# ======================

@staff_member_required
def admin_dashboard(request):

    # 💰 Total Revenue
    total_revenue = Booking.objects.aggregate(
        total=Sum('price')
    )['total'] or 0

    # 🎬 Most Popular Movies
    popular_movies = (
        Booking.objects
        .values('movie__name')
        .annotate(total_bookings=Count('id'))
        .order_by('-total_bookings')[:5]
    )

    # 🏢 Busiest Theaters
    busiest_theaters = (
        Booking.objects
        .values('theater__name')
        .annotate(total_bookings=Count('id'))
        .order_by('-total_bookings')[:5]
    )

    return render(request, 'movies/admin_dashboard.html', {
        'total_revenue': total_revenue,
        'popular_movies': popular_movies,
        'busiest_theaters': busiest_theaters,
    })
