// for trends.html
// use d3 to draw line graph
var margin = {top: 40, right: 50, bottom: 40, left: 50},
    width = 1280 - margin.left - margin.right,
    height = 865 - margin.top - margin.bottom;


// changes based on what is selected
var currentState = "seats"

// changes with what user selects from #farleft
var graphParties = ["conservative", "labour", "libdems", "ukip", "snp", "plaidcymru", "green"]

var parseDate = d3.time.format("%Y-%m-%d").parse;

function drawGraph(type){

  d3.selectAll("path")
       .remove();
  d3.selectAll("text")
        .remove();

  d3.selectAll(".axis")
        .remove();

  var x = d3.time.scale().range([0, width]);
  var y = d3.scale.linear().range([height, 0]);
  var y1 = d3.scale.linear().range([height, 0]);

  var xAxis = d3.svg.axis().scale(x)
    .orient("bottom").ticks(8)

  var yAxis = d3.svg.axis().scale(y)
    .orient("left").ticks(25);

  var yAxis1 = d3.svg.axis().scale(y)
    .orient("right").ticks(25);	


  var line = d3.svg.line()
      .x(function(d) { return x(d.day); })
      .y(function(d) { if (type == "seats") return y(d.seats); if (type =="percent") return y(d.percent)});

  d3.tsv("/election2015/data/trends.csv", function(error, data) {

      var filteredData = [];

      data.forEach(function(d) {
        if (graphParties.indexOf(d.party) != -1){
          d.day = parseDate(d.day);
          filteredData.push(d);
        }

      });


      filteredData.forEach(function(d) {

          if (type == "seats"){
            d.seats = +d.seats;
          }

          if (type == "percent"){
            d.percent = +d.percent;
          }


      });

      x.domain(d3.extent(filteredData, function(d) { return d.day; }));

      var yMinimum = d3.min(filteredData, function(d) {

        if (type == "seats"){
          value = d.seats - 20;
          if (value < 0){
            value = 0
          }
          return value;
        }

        if (type == "percent"){
          value = d.percent - 10;
          if (value < 0){
            value = 0
          }
          return value;

        }
      });

      var yMaximum = d3.max(filteredData, function(d) {
        if (type == "seats")
          return d.seats + 5;
        if (type == "percent")
          return d.percent + 2; });

      y.domain([yMinimum, yMaximum]);


      var dataNest = d3.nest()
          .key(function(d) {return d.party;})
          .entries(filteredData);


      dataNest.forEach(function(d) {
        svg.append("path")
          .attr("class", d.key)
          .style("fill", "none")
          .attr("d", line(d.values))

      });


      filteredData.forEach(function(d) {

          svg.selectAll("dot")
              .data(filteredData)


        });

      svg.append("g")
          .attr("class", "x axis")
          .attr("class", "axis")
          .attr("transform", "translate(0," + height + ")")
          .attr("transform", "translate(0," + height + ")")
          .call(xAxis);

      svg.append("g")
          .attr("class", "y axis")
          .attr("class", "axis")
          .call(yAxis);
		  
	 svg.append("g")
          .attr("class", "y axis")
		  .attr("transform", "translate(" + width + " ,0)")	
          .attr("class", "axis")
          .call(yAxis1);

  	  svg.append("text")
    	    .attr("class", "x label")
    	    .attr("text-anchor", "end")
    	    .attr("x", width / 2)
    	    .attr("y", height + 35)
    	    .text("Date");


    	svg.append("text")
        .attr("class", "y label")
        .attr("text-anchor", "end")
        .attr("y", - 50)
        .attr("x", -height / 2 + 100)
        .attr("dy", ".75em")
        .attr("transform", "rotate(-90)")
        .text("Seats / Percentages");
  });

}

drawGraph(currentState);

function reDrawGraph(party){
  var indexOfParty = graphParties.indexOf(party)

  if (indexOfParty >= 0){
    graphParties.splice(indexOfParty, 1)
  }

  if (indexOfParty < 0){
    graphParties.push(party)
  }

  drawGraph(currentState);
}
