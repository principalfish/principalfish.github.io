var plot = {

  drawGridlines: function(){

    var yMax = 65;

    x.domain([new Date(2015, 4, 6), new Date(2017, 5, 8)]);
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
        .data([modelData[party]])
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

    var date1 = new Date(2015, 4, 6).getTime()
    var date2 = Date.now()

    var months = Math.floor((date2 - date1) / (1000 * 60 * 60 * 24 * 365 / 12))

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
      var majData =
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
