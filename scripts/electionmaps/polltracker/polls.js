var pollData = {
	"labour" : [],
	"conservative"  : [],
	"libdems" : [],
	"snp": [],
	"green": [],
	"ukip" : [],
	"others" : []
}

var modelData = {
	"labour" : [],
	"conservative"  : [],
	"libdems" : [],
	"snp": [],
	"green": [],
	"ukip" : [],
	"others" : []
}

var modelDataDisplay = {

}

var companies = []

function getData(){
	$.when(
		$.getJSON("polltracker/scatter1.json", function(data){

			$.each(data["polls"], function(poll, numbers){

				var date = new Date(numbers["date"][2], numbers["date"][1] - 1, numbers["date"][0])
			  $.each(pollData, function(party){

					if (companies.indexOf(numbers["company"]) == -1){
						companies.push(numbers["company"]);
					}

					pollData[party].push({"company": numbers["company"],
																"date" : date,
																"percentage" : numbers[party]});
				})
			})

			$.each(data["models"], function(seats, data){

					var dateObj = new Date(Date.parse(data.date))

					$.each(data, function(key, val){
						if (key != "date"){

							modelData[key].push({
								"date" : dateObj,
								"seats" : val
							})
						}
					})
			});
		})

	).then(function(){
		pageLoad();
	})
};

function pageLoad(){

	plot.drawGridlines();

	// general model data for display
	$.each(modelData, function(party, data){
		var modelInDates = [];

		for (var i=0; i < data.length; i++){

			if (inDateRange(data[i].date)){
				var toAdd = {
					"date" : data[i].date,
					"seats" : data[i].seats
				}

				modelInDates.push(toAdd);
			}
		}

		modelDataDisplay[party] = modelInDates;
	})

	$.each(pollData, function(party, data){

		var relevantPoints = [];

		var toDraw = [];

		for (var i=0; i < data.length; i++){

			if (companies.indexOf(data[i].company) != -1 && inDateRange(data[i].date)){
				var to_add = {
					"company" : data[i].company,
					"percentage" : data[i].percentage,
					"date" : data[i].date,
					"moving" : null
				};

				relevantPoints.push(data[i].percentage);
				if (relevantPoints.length > 12){
					relevantPoints.splice(0, 1);
				}
				var total = 0;
				for (percentage in relevantPoints){
					total += relevantPoints[percentage];
				}

				var avg =  total / relevantPoints.length;
				to_add.moving = avg;
				toDraw.push(to_add);
			}
		}

		plot.draw(toDraw, party);
	});


	plot.drawAxes();
}

function inDateRange(date){
	var inRange = date >= filter.datestart && date <= filter.dateend;
	return inRange;
}

$(document).ready(function(){
	getData();
});
