var filter = {
  pollsDisplay : true,
  modelDisplay : true,
  movingDisplay: true,

  datestart : new Date(2015, 4, 6),
  dateend : new Date(2017, 5, 8),

  date : function(value, id){

    if (id == "dateselect-start"){
      filter.datestart = new Date(value);
    }

    if (id == "dateselect-end"){
      filter.dateend = new Date(value);
    }

    var defStart = new Date(2015, 4, 6);
    var defEnd =  new Date(2017, 5, 8);

    if (filter.datestart <= defStart || filter.datestart == "Invalid Date"){
      filter.datestart = defStart;
    }

    if (filter.dateend >= defEnd || filter.dateend == "Invalid Date"){
      filter.dateend = defEnd;
    }

    filter.redraw();
  },

  company : function(value){
    if (companies.indexOf(value) == -1){
      companies.push(value);
    } else {
      var i = companies.indexOf(value);
      companies.splice(i, 1)
    }

    filter.redraw();
  },

  redraw: function(){
    $("#plot g").empty()
    pageLoad();
  }
}
;var plot = {

  drawGridlines: function(){

    var yMax = 65;

    x.domain([filter.datestart, filter.dateend]);
    y.domain([0, yMax]);
    y2.domain([0, 650 * yMax / 100]);

    // add the X gridlines
    svg.append("g")
        .attr("class", "grid")
        .attr("transform", "translate(0," + height + ")")
        .call(plot.makeXGridlines()
            .tickSize(-height)
            .tickFormat("")
        )
    // add the Y gridlines
    svg.append("g")
        .attr("class", "grid")
        .call(plot.makeYGridlines()
            .tickSize(-width)
            .tickFormat("")
        )
  },

  draw : function(data, party){

    //Add the valueline path.
    if (filter.movingDisplay == true){
      svg.append("path")
        .data([data])
        .attr("class", "line " + party)
        .attr("d", valueline);
      }


    // Add the scatterplot
    if (filter.pollsDisplay == true){
      svg.selectAll("dot")
          .data(data)
        .enter().append("circle")
          .attr("r", 1)
          .attr("cx", function(d) { return x(d.date); })
          .attr("cy", function(d) { return y(d.percentage); })
          .attr("class", party)
          .append("svg:title")
  					.text(function(d) { return d.company + " "  +
                                        d.date.getDate() + "/" +
                                        (d.date.getMonth() + 1) + "/" +
                                        d.date.getFullYear() + " : " + d.percentage + "%"});
       }


    if (filter.modelDisplay == true){

      svg.append("path")
        .data([modelDataDisplay[party]])
        .attr("class", "modelline " + party)
        .attr("d", modelline)
        .style("stroke-dasharray", "10, 5")
    }
  },

  makeXGridlines : function(){
    return d3.axisBottom(x)
     .ticks(5)
  },

  makeYGridlines : function(){
    return d3.axisLeft(y)
       .ticks(10)
  },

  drawAxes : function(){

    var date1 = filter.datestart;
    var date2 = filter.dateend;

    var months = Math.floor((date2 - date1) / (1000 * 60 * 60 * 24 * 365 / 12))

    if (months > 25){
      months = 25;
    }

    svg.append("g")
        .attr("transform", "translate(0," + height + ")")
        .call(d3.axisBottom(x).ticks(months).tickFormat(timeFormat))
        .selectAll("text")
          .style("text-anchor", "end")
          .attr("dx", "-.8em")
          .attr("dy", ".15em")
          .attr("transform", "rotate(-45)");

    // Add the Y Axis
    svg.append("g")
        .call(d3.axisLeft(y));

    // add right y axis
    svg.append("g")
      .call(d3.axisRight(y2).ticks(15))
      .attr("transform", "translate(" + width + " , 0)")


    // add x axis label

    svg.append("text")
      .attr("transform",
            "translate(" + (width/2) + " ," +
                           (height + margin.top + 30) + ")")
      .style("text-anchor", "middle")
      .text("Date");


    // text label for the y axis
    svg.append("text")
        .attr("transform", "rotate(-90)")
        .attr("y", 0 - margin.left)
        .attr("x",0 - (height / 2))
        .attr("dy", "1em")
        .style("text-anchor", "middle")
        .text("Percentage");


    // label for right y axis
    svg.append("text")
      .attr("transform", "rotate(90)")
      .attr("y", -width - margin.right)
      .attr("x",0 + (height / 2))
      .attr("dy", "1em")
      .style("text-anchor", "middle")
      .text("Seats");

    // draw majority line
    if (filter.modelDisplay == true){

      svg.append("path")
        .data([[{"date" : date1, "seats" : 326}, {"date" : date2, "seats" : 326}]])
        .attr("class", "majorityline")
        .attr("d", majorityline)
        .style("stroke-dasharray", "2, 2")
    }


  }

}

var margin = {
            top: 20,
            right: 40,
            bottom: 60,
            left: 40
        },
        width = 1000 - margin.left - margin.right,
        height = 800 - margin.top - margin.bottom;

var parseTime = d3.timeParse("%d-%b-%y");

var x = d3.scaleTime().range([0, width]);
var y = d3.scaleLinear().range([height, 0]);
var y2 = d3.scaleLinear().range([height, 0])

var timeFormat = d3.timeFormat("%b %y")
var valueline = d3.line()
    .x(function(d) { return x(d.date); })
    .y(function(d) { return y(d.moving); });

var modelline = d3.line()
    .x(function(d) { return x(d.date); })
    .y(function(d) { return y2(d.seats); });

var majorityline = d3.line()
  .x(function(d) {return x(d.date);})
  .y(function(d) {return y2(d.seats)})
;var pollData = {
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
