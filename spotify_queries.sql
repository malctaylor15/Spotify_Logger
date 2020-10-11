-- Songs by date in last week 
select played_at_date, count(*) 
, count(distinct name) as unique_songs
, sum(duration_min) as min_listened
from Listening_History
where played_at_date between '2020-08-13' and '2020-08-20'
group by played_at_date
order by 1 desc;



select artist_name, count(*), count(distinct(name))
, sum(duration_min) as total_mins
from Listening_History
where played_at_date between '2020-08-13' and '2020-08-20'
group by artist_name
order by 2 desc;



